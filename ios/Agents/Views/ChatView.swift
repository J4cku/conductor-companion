import PhotosUI
import SwiftUI

/// One chat: a live transcript, what the agent is doing right now, a question card when it's asking,
/// quick replies, dictation, photos, and the message box.
struct ChatView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.horizontalSizeClass) private var size
    let sessionID: String

    @State private var transcript: Transcript?
    @State private var problem: String?
    @State private var offline = false
    @State private var draft = ""
    @State private var sending = false
    @State private var sent = 0
    @State private var toast: String?
    @State private var atBottom = true
    @State private var position = ScrollPosition(edge: .bottom)
    @State private var photo: PhotosPickerItem?
    @State private var camera = false
    @State private var changes = false
    @State private var dictation = Dictation()
    @FocusState private var typing: Bool
    @Namespace private var glass

    private static let quickReplies = ["Continue", "Run the tests", "Open a PR", "Explain what you did", "Stop and wait for me"]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if let transcript {
                    if transcript.entries.isEmpty {
                        ContentUnavailableView("No messages yet", systemImage: "bubble.left.and.text.bubble.right").padding(.top, 80)
                    }
                    ForEach(ChatItem.group(transcript.entries)) { item in
                        ChatItemView(item: item, tint: model.accent)
                    }
                } else if let problem {
                    ContentUnavailableView("Can't load this chat", systemImage: "exclamationmark.bubble", description: Text(problem)).padding(.top, 80)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 80)
                }
                Color.clear.frame(height: 1).onScrollVisibilityChange(threshold: 0.01) { visible in
                    withAnimation(.snappy) { atBottom = visible }
                }
            }
            .scrollTargetLayout()
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .scrollPosition($position)
        .defaultScrollAnchor(.bottom, for: .initialOffset)
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .scrollDismissesKeyboard(.interactively)
        .background { AmbientBackground(tint: model.accent, intensity: 0.35) }
        .scrollEdgeEffectStyle(.soft, for: .all)
        .safeAreaBar(edge: .top, spacing: 0) { currentStep }
        .safeAreaBar(edge: .bottom, spacing: 0) { dock }
        .overlay(alignment: .bottomTrailing) { jumpToLatest }
        .overlay(alignment: .top) { toastView }
        .navigationTitle(transcript?.session?.name ?? "Chat")
        .navigationSubtitle(subtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(size == .compact ? .hidden : .automatic, for: .tabBar)
        .toolbar {
            if transcript?.session?.workspace != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Changes", systemImage: "plusminus.circle") { changes = true }
                }
            }
        }
        .sheet(isPresented: $changes) {
            if let workspace = transcript?.session?.workspace { ChangesView(workspace: workspace, title: transcript?.session?.name ?? "") }
        }
        .fullScreenCover(isPresented: $camera) {
            CameraPicker { image in send(image: image) }.ignoresSafeArea()
        }
        .onChange(of: photo) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) { send(image: image) }
                photo = nil
            }
        }
        .onChange(of: dictation.transcript) { _, text in if dictation.listening, !text.isEmpty { draft = text } }
        .sensoryFeedback(.success, trigger: sent)
        .animation(.snappy, value: toast)
        .animation(.snappy, value: transcript?.held)
        .task(id: sessionID) { await follow() }
        #if DEBUG
        .task { if ProcessInfo.processInfo.arguments.contains("-changes") { try? await Task.sleep(for: .seconds(1)); changes = true } }
        #endif
    }

    // MARK: Pieces

    private var subtitle: String {
        guard let session = transcript?.session else { return "" }
        let label = model.snapshot?.theme.label(session.state) ?? session.state.rawValue
        return [offline ? "Offline" : nil, session.place == session.name ? nil : session.place, session.unit, label, money(session.cost)].compactMap { $0 }.joined(separator: " · ")
    }

    private var question: Question? { transcript?.held?.question }

    private var placeholder: String {
        if question != nil { return "Answer…" }
        if dictation.listening { return "Listening…" }
        return transcript?.reachable == false ? "Agent stopped — open it in Conductor" : "Message"
    }

    /// What the agent is doing right now, pinned under the title while it works.
    @ViewBuilder private var currentStep: some View {
        if let session = transcript?.session, session.state == .working {
            let step = transcript?.entries.last(where: { $0.role == .tool })?.text ?? "Thinking"
            HStack(spacing: 10) {
                StateIcon(state: .working, size: 20)
                Text(step).font(.footnote.weight(.medium)).lineLimit(1)
                Spacer(minLength: 6)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(ago(session.since, now: context.date)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 9)
            .glassEffect(.regular.tint(model.accent.opacity(0.15)), in: .capsule)
            .padding(.horizontal, 14).padding(.top, 6)
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }

    private var dock: some View {
        VStack(spacing: 10) {
            if let question { QuestionCard(question: question, tint: model.accent) { send($0) } }
            if draft.isEmpty, !typing, question == nil, transcript?.reachable != false {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Self.quickReplies, id: \.self) { reply in
                            Button(reply) { send(reply) }.buttonStyle(.glass).controlSize(.small)
                        }
                    }
                    .padding(.horizontal, 2)
                }
                .scrollClipDisabled()
                .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
            GlassEffectContainer(spacing: 10) {
                HStack(alignment: .bottom, spacing: 10) {
                    Menu {
                        PhotosPicker(selection: $photo, matching: .images) { Label("Photo library", systemImage: "photo.on.rectangle") }
                        if UIImagePickerController.isSourceTypeAvailable(.camera) {
                            Button("Camera", systemImage: "camera") { camera = true }
                        }
                    } label: {
                        Image(systemName: "plus").font(.body.weight(.semibold)).frame(width: 30, height: 30)
                    }
                    .buttonStyle(.glass)
                    .buttonBorderShape(.circle)
                    .glassEffectID("attach", in: glass)

                    TextField(placeholder, text: $draft, axis: .vertical)
                        .lineLimit(1...6)
                        .focused($typing)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .glassEffect(.regular.interactive(), in: .rect(cornerRadius: 22))
                        .glassEffectID("field", in: glass)

                    if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || dictation.listening {
                        Button {
                            Task { dictation.listening ? dictation.stop() : await dictation.start() }
                        } label: {
                            Image(systemName: dictation.listening ? "waveform" : "mic.fill")
                                .font(.body.weight(.semibold)).frame(width: 30, height: 30)
                                .symbolEffect(.variableColor.iterative, isActive: dictation.listening)
                        }
                        .buttonStyle(.glass)
                        .buttonBorderShape(.circle)
                        .tint(dictation.listening ? .red : nil)
                        .glassEffectID("action", in: glass)
                    } else {
                        Button {
                            send(draft)
                        } label: {
                            Image(systemName: sending ? "ellipsis" : "arrow.up").font(.body.weight(.bold)).frame(width: 30, height: 30)
                                .contentTransition(.symbolEffect(.replace))
                        }
                        .buttonStyle(.glassProminent)
                        .buttonBorderShape(.circle)
                        .disabled(sending)
                        .glassEffectID("action", in: glass)
                    }
                }
            }
            if let issue = dictation.problem { Text(issue).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 8)
        .animation(.snappy, value: draft.isEmpty)
        .animation(.snappy, value: typing)
    }

    @ViewBuilder private var jumpToLatest: some View {
        if !atBottom {
            Button {
                withAnimation(.smooth) { position.scrollTo(edge: .bottom) }
            } label: {
                Image(systemName: "arrow.down").font(.body.weight(.semibold)).frame(width: 26, height: 26)
            }
            .buttonStyle(.glass)
            .buttonBorderShape(.circle)
            .padding(.trailing, 18)
            .padding(.bottom, 130)
            .transition(.scale.combined(with: .opacity))
        }
    }

    @ViewBuilder private var toastView: some View {
        if let toast {
            Text(toast).font(.footnote).padding(.horizontal, 14).padding(.vertical, 9)
                .glassEffect(.regular, in: .capsule).padding(.top, 52)
                .transition(.move(edge: .top).combined(with: .opacity))
        }
    }

    // MARK: Data

    /// The live stream when the Mac offers one, polling otherwise.
    private func follow() async {
        guard let client = model.client else { return }
        if transcript == nil, let cached = TranscriptCache.read(sessionID) { transcript = cached }
        await load()
        while !Task.isCancelled {
            do {
                for try await (event, payload) in client.events(session: sessionID) where event == "transcript" {
                    if let next = try? JSONDecoder().decode(Transcript.self, from: payload), next != transcript {
                        withAnimation(.snappy) { transcript = next }
                        TranscriptCache.write(next, for: sessionID)
                    }
                }
            } catch {}
            await load()
            try? await Task.sleep(for: .seconds(2))
        }
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            let next = try await client.transcript(sessionID)
            if next != transcript {
                withAnimation(.snappy) { transcript = next }
                TranscriptCache.write(next, for: sessionID)
            }
            problem = nil
            offline = false
        } catch {
            if transcript == nil { problem = error.localizedDescription }
            offline = true
        }
    }

    private func send(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        dictation.stop()
        sending = true
        Task {
            defer { sending = false }
            do {
                let result = try await model.send(text, to: sessionID)
                if text == draft.trimmingCharacters(in: .whitespacesAndNewlines) { draft = "" }
                sent += 1
                flash(result.detail)
                await load()
                withAnimation(.smooth) { position.scrollTo(edge: .bottom) }
            } catch {
                flash(error.localizedDescription)
            }
        }
    }

    private func send(image: UIImage) {
        guard let client = model.client, let data = image.preparedForUpload else { return }
        let caption = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        sending = true
        Task {
            defer { sending = false }
            do {
                let result = try await client.attach(sessionID, name: "photo.jpg", mime: "image/jpeg", data: data,
                                                     caption: caption.isEmpty ? "Here's a photo from my phone." : caption)
                draft = ""
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

extension UIImage {
    /// At most 2048 px on the long side, as JPEG: big enough to read a whiteboard, small enough to send fast.
    var preparedForUpload: Data? {
        let longest = max(size.width, size.height)
        guard longest > 0 else { return nil }
        let scale = min(1, 2048 / longest)
        let target = CGSize(width: size.width * scale, height: size.height * scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: target, format: format).jpegData(withCompressionQuality: 0.82) { _ in
            draw(in: CGRect(origin: .zero, size: target))
        }
    }
}

struct CameraPicker: UIViewControllerRepresentable {
    @Environment(\.dismiss) private var dismiss
    let taken: (UIImage) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker

        init(parent: CameraPicker) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage { parent.taken(image) }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
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
    let tint: Color

    var body: some View {
        switch item {
        case let .you(entry):
            VStack(alignment: .trailing, spacing: 3) {
                Text(entry.text)
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .glassEffect(.regular.tint(tint.opacity(0.28)), in: .rect(cornerRadius: 20))
                    .textSelection(.enabled)
                if entry.role == .phone, let status = entry.status {
                    Label(["sending": "Sending…", "delivered": "Sent from phone", "failed": "Not delivered"][status] ?? status,
                          systemImage: status == "failed" ? "exclamationmark.circle" : "iphone")
                        .font(.caption2).foregroundStyle(status == "failed" ? .red : .secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
            .padding(.leading, 48)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        case let .agent(entry):
            AgentReply(text: entry.text)
        case let .note(entry):
            Text(entry.text).font(.footnote).foregroundStyle(.secondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Capsule().fill(tint).frame(width: 2) }
        case let .steps(entries):
            StepsView(entries: entries)
        }
    }
}

/// An agent reply, folded after the first screenful.
struct AgentReply: View {
    let text: String
    @State private var expanded = false

    private var long: Bool { text.count > 1600 }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            MarkdownView(long && !expanded ? String(text.prefix(1200)) + "…" : text)
            if long {
                Button(expanded ? "Show less" : "Show more") { withAnimation(.snappy) { expanded.toggle() } }
                    .font(.footnote.weight(.semibold))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
    let tint: Color
    let answer: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(question.question, systemImage: "questionmark.bubble.fill")
                .font(.subheadline.weight(.semibold))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(.orange)
            if let options = question.options, !options.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(options, id: \.self) { option in
                            Button(option.label) { answer(option.label) }.buttonStyle(.glass)
                        }
                    }
                }
                .scrollClipDisabled()
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassEffect(.regular.tint(Color.orange.opacity(0.18)), in: .rect(cornerRadius: 22))
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

/// The last transcript of each chat, so a chat opens instantly and still reads when the Mac is out of reach.
enum TranscriptCache {
    private static let folder = URL.cachesDirectory.appending(path: "transcripts", directoryHint: .isDirectory)

    static func read(_ session: String) -> Transcript? {
        (try? Data(contentsOf: file(session))).flatMap { try? JSONDecoder().decode(Transcript.self, from: $0) }
    }

    static func write(_ transcript: Transcript, for session: String) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try? JSONEncoder().encode(transcript).write(to: file(session), options: [.atomic, .completeFileProtection])
    }

    private static func file(_ session: String) -> URL {
        folder.appending(path: session.filter { $0.isLetter || $0.isNumber || $0 == "-" } + ".json")
    }
}
