import SwiftUI

/// One chat: its transcript, a question card when the agent is asking, and the message box.
struct ChatView: View {
    @Environment(AppModel.self) private var model
    let sessionID: String

    @State private var transcript: Transcript?
    @State private var problem: String?
    @State private var draft = ""
    @State private var sending = false
    @State private var sent = 0
    @State private var toast: String?
    @FocusState private var typing: Bool

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                if let transcript {
                    if transcript.entries.isEmpty {
                        ContentUnavailableView("No messages yet", systemImage: "bubble.left.and.text.bubble.right").padding(.top, 80)
                    }
                    ForEach(ChatItem.group(transcript.entries)) { item in
                        ChatItemView(item: item)
                    }
                } else if let problem {
                    ContentUnavailableView("Can't load this chat", systemImage: "exclamationmark.bubble", description: Text(problem)).padding(.top, 80)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 80)
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .defaultScrollAnchor(.bottom, for: .initialOffset)
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom, spacing: 0) { dock }
        .navigationTitle(transcript?.session?.name ?? "Chat")
        .navigationSubtitle(subtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .tabBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if let session = transcript?.session { StateIcon(state: session.state, size: 24) }
            }
        }
        .sensoryFeedback(.success, trigger: sent)
        .overlay(alignment: .top) {
            if let toast {
                Text(toast).font(.footnote).padding(.horizontal, 14).padding(.vertical, 9)
                    .glassEffect(.regular, in: .capsule).padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.snappy, value: toast)
        .task(id: sessionID) {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private var subtitle: String {
        guard let session = transcript?.session else { return "" }
        let label = model.snapshot?.theme.label(session.state) ?? session.state.rawValue
        return [session.place == session.name ? nil : session.place, session.unit, label].compactMap { $0 }.joined(separator: " · ")
    }

    private var question: Question? { transcript?.held?.question }

    private var placeholder: String {
        if question != nil { return "Answer…" }
        return transcript?.reachable == false ? "Agent stopped — open it in Conductor" : "Message"
    }

    private var dock: some View {
        VStack(spacing: 10) {
            if let question { QuestionCard(question: question) { send($0) } }
            GlassEffectContainer(spacing: 10) {
                HStack(alignment: .bottom, spacing: 10) {
                    TextField(placeholder, text: $draft, axis: .vertical)
                        .lineLimit(1...6)
                        .focused($typing)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .glassEffect(.regular, in: .rect(cornerRadius: 22))
                    Button {
                        send(draft)
                    } label: {
                        Image(systemName: sending ? "ellipsis" : "arrow.up")
                            .font(.body.weight(.bold))
                            .frame(width: 30, height: 30)
                            .contentTransition(.symbolEffect(.replace))
                    }
                    .buttonStyle(.glassProminent)
                    .buttonBorderShape(.circle)
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sending)
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 8)
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            let next = try await client.transcript(sessionID)
            if next != transcript { transcript = next }
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    private func send(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        Task {
            defer { sending = false }
            do {
                let result = try await model.send(text, to: sessionID)
                if text == draft.trimmingCharacters(in: .whitespacesAndNewlines) { draft = "" }
                sent += 1
                flash(result.detail)
                await load()
            } catch {
                flash(error.localizedDescription)
            }
        }
    }

    private func flash(_ text: String) {
        toast = text
        Task {
            try? await Task.sleep(for: .seconds(3))
            if toast == text { toast = nil }
        }
    }
}

/// Consecutive tool calls fold into one expandable "steps" item.
enum ChatItem: Identifiable {
    case you(Entry)
    case agent(Entry)
    case note(Entry)
    case steps([Entry])

    var id: String {
        switch self {
        case let .you(entry), let .agent(entry), let .note(entry): return "\(entry.role.rawValue)|\(entry.at ?? "")|\(entry.text.prefix(40))"
        case let .steps(entries): return "steps|\(entries.first?.at ?? "")|\(entries.first?.text.prefix(40) ?? "")"
        }
    }

    static func group(_ entries: [Entry]) -> [ChatItem] {
        var items: [ChatItem] = []
        var steps: [Entry] = []
        for entry in entries {
            if entry.role == .tool { steps.append(entry); continue }
            if !steps.isEmpty { items.append(.steps(steps)); steps = [] }
            switch entry.role {
            case .user, .phone: items.append(.you(entry))
            case .assistant: items.append(.agent(entry))
            case .note: items.append(.note(entry))
            case .tool: break
            }
        }
        if !steps.isEmpty { items.append(.steps(steps)) }
        return items
    }
}

struct ChatItemView: View {
    let item: ChatItem

    var body: some View {
        switch item {
        case let .you(entry):
            VStack(alignment: .trailing, spacing: 3) {
                Text(entry.text)
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .background(Color.accentColor.opacity(0.18), in: .rect(cornerRadius: 20))
                    .textSelection(.enabled)
                if entry.role == .phone, let status = entry.status {
                    Text(["sending": "Sending…", "delivered": "Sent from phone", "failed": "Not delivered"][status] ?? status)
                        .font(.caption2).foregroundStyle(status == "failed" ? .red : .secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
            .padding(.leading, 48)
        case let .agent(entry):
            MarkdownView(entry.text).frame(maxWidth: .infinity, alignment: .leading)
        case let .note(entry):
            Text(entry.text).font(.footnote).foregroundStyle(.secondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Capsule().fill(Color.accentColor).frame(width: 2) }
        case let .steps(entries):
            StepsView(entries: entries)
        }
    }
}

struct StepsView: View {
    let entries: [Entry]
    @State private var open = false

    var body: some View {
        DisclosureGroup(isExpanded: $open) {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                    Text(entry.text).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            .padding(.top, 4)
        } label: {
            Label(entries.count == 1 ? entries[0].text : "\(entries.count) steps · \(entries.last?.text ?? "")", systemImage: "terminal")
                .font(.footnote).foregroundStyle(.secondary).lineLimit(1)
        }
        .tint(.secondary)
    }
}

/// The agent's question, answerable with one tap.
struct QuestionCard: View {
    let question: Question
    let answer: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(question.question, systemImage: "questionmark.bubble.fill")
                .font(.subheadline.weight(.semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(Color.accentColor)
            if let options = question.options, !options.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(options, id: \.self) { option in
                            Button(option.label) { answer(option.label) }.buttonStyle(.glass)
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassEffect(.regular.tint(Color.accentColor.opacity(0.15)), in: .rect(cornerRadius: 20))
    }
}
