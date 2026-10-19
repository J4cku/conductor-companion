import SwiftUI

/// What the agent changed in a workspace: diff stats against the base branch, the pull request and its checks.
struct ChangesView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    let workspace: String
    let title: String

    @State private var changes: Changes?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            List {
                if let changes {
                    Section { totals(changes) }.listRowBackground(Color.clear).listRowInsets(.init())
                    if let pr = changes.pr { Section("Pull request") { pullRequest(pr) } }
                    Section(changes.files.isEmpty ? "No changes yet" : "Files") {
                        ForEach(changes.files) { file in
                            NavigationLink {
                                FileDiffView(workspace: workspace, file: file)
                            } label: {
                                FileRow(file: file)
                            }
                        }
                    }
                } else if let problem {
                    ContentUnavailableView("Can't load changes", systemImage: "exclamationmark.triangle", description: Text(problem))
                        .listRowBackground(Color.clear)
                } else {
                    ProgressView().frame(maxWidth: .infinity).listRowBackground(Color.clear)
                }
            }
            .scrollContentBackground(.hidden)
            .background { AmbientBackground(tint: model.accent, intensity: 0.4) }
            .navigationTitle("Changes")
            .navigationSubtitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { dismiss() } }
            }
            .refreshable { await load() }
            .task { await load() }
        }
        .presentationDetents([.medium, .large])
        .presentationBackground(.clear)
    }

    private func totals(_ changes: Changes) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 0) {
                stat("\(changes.totals.files)", changes.totals.files == 1 ? "file" : "files", .primary)
                Spacer()
                stat("+\(changes.totals.added)", "added", .green)
                Spacer()
                stat("−\(changes.totals.removed)", "removed", .red)
            }
            if let branch = changes.branch {
                Label([branch, changes.base].compactMap { $0 }.joined(separator: " → "), systemImage: "arrow.triangle.branch")
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
        }
        .padding(.horizontal, 22).padding(.vertical, 16)
        .glassCard(model.accent, radius: 26)
        .padding(.vertical, 4)
    }

    private func stat(_ value: String, _ label: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(.title, design: .rounded, weight: .bold)).foregroundStyle(tint).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func pullRequest(_ pr: PullRequest) -> some View {
        Button {
            if let url = URL(string: pr.url) { openURL(url) }
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: pr.state == "MERGED" ? "arrow.triangle.merge" : "arrow.triangle.pull").foregroundStyle(prTint(pr))
                    Text(verbatim: "#\(pr.number)").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                    Text(pr.title).font(.subheadline.weight(.semibold)).lineLimit(2)
                }
                HStack(spacing: 10) {
                    Badge(text: pr.isDraft == true ? "draft" : pr.state.lowercased(), tint: prTint(pr))
                    if let decision = pr.reviewDecision, !decision.isEmpty {
                        Text(decision.replacingOccurrences(of: "_", with: " ").lowercased()).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if let checks = pr.checks { ChecksSummary(checks: checks) }
                }
            }
        }
        .tint(.primary)
    }

    private func prTint(_ pr: PullRequest) -> Color {
        if pr.isDraft == true { return .gray }
        switch pr.state {
        case "MERGED": return .purple
        case "CLOSED": return .red
        default: return .green
        }
    }

    private func load() async {
        guard let client = model.client else { return }
        do {
            let next = try await client.changes(workspace: workspace)
            withAnimation(.snappy) { changes = next }
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

struct ChecksSummary: View {
    let checks: PullRequest.Checks

    var body: some View {
        HStack(spacing: 8) {
            if checks.failed > 0 { Label("\(checks.failed)", systemImage: "xmark.circle.fill").foregroundStyle(.red) }
            if checks.pending > 0 { Label("\(checks.pending)", systemImage: "clock.fill").foregroundStyle(.orange) }
            if checks.passed > 0 { Label("\(checks.passed)", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
        }
        .font(.caption.monospacedDigit())
        .labelStyle(.titleAndIcon)
    }
}

struct FileRow: View {
    let file: ChangedFile

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).foregroundStyle(tint).frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text((file.path as NSString).lastPathComponent).font(.subheadline.weight(.medium)).lineLimit(1)
                let folder = (file.path as NSString).deletingLastPathComponent
                if !folder.isEmpty { Text(folder).font(.caption2).foregroundStyle(.secondary).lineLimit(1).truncationMode(.head) }
            }
            Spacer(minLength: 6)
            DiffBar(added: file.added, removed: file.removed)
        }
    }

    private var symbol: String {
        switch file.status {
        case "A": "plus.square.fill"
        case "D": "minus.square.fill"
        case "R": "arrow.right.square.fill"
        default: "square.and.pencil"
        }
    }

    private var tint: Color {
        switch file.status {
        case "A": .green
        case "D": .red
        default: .secondary
        }
    }
}

/// `+12 −3` with GitHub's five little squares.
struct DiffBar: View {
    let added: Int
    let removed: Int

    var body: some View {
        let total = max(added + removed, 1)
        let green = added + removed == 0 ? 0 : Int((Double(added) / Double(total) * 5).rounded())
        HStack(spacing: 6) {
            Text("+\(added)").foregroundStyle(.green)
            Text("−\(removed)").foregroundStyle(.red)
            HStack(spacing: 1.5) {
                ForEach(0..<5, id: \.self) { index in
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(index < green ? Color.green : added + removed == 0 ? Color.secondary.opacity(0.3) : Color.red)
                        .frame(width: 6, height: 6)
                }
            }
        }
        .font(.caption.monospacedDigit())
    }
}

/// One file's unified diff, coloured.
struct FileDiffView: View {
    @Environment(AppModel.self) private var model
    let workspace: String
    let file: ChangedFile
    @State private var diff: FileDiff?
    @State private var problem: String?
    @State private var wrap = false

    var body: some View {
        Group {
            if let diff {
                ScrollView(wrap ? .vertical : [.vertical, .horizontal]) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(diff.patch.split(separator: "\n", omittingEmptySubsequences: false).enumerated()), id: \.offset) { _, line in
                            DiffLine(line: String(line))
                        }
                        if diff.truncated == true {
                            Text("Diff truncated — open it on the Mac for the rest.").font(.footnote).foregroundStyle(.secondary).padding()
                        }
                    }
                    .padding(.vertical, 8)
                }
            } else if let problem {
                ContentUnavailableView("Can't load the diff", systemImage: "exclamationmark.triangle", description: Text(problem))
            } else {
                ProgressView()
            }
        }
        .navigationTitle((file.path as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(wrap ? "Don't wrap" : "Wrap lines", systemImage: wrap ? "arrow.left.and.right.text.vertical" : "text.word.spacing") { wrap.toggle() }
            }
        }
        .task {
            guard let client = model.client else { return }
            do { diff = try await client.diff(workspace: workspace, path: file.path) } catch { problem = error.localizedDescription }
        }
    }
}

struct DiffLine: View {
    let line: String

    var body: some View {
        Text(line.isEmpty ? " " : line)
            .font(.system(size: 12, design: .monospaced))
            .foregroundStyle(foreground)
            .padding(.horizontal, 12).padding(.vertical, 1)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(background)
    }

    private var kind: Character? {
        if line.hasPrefix("+++") || line.hasPrefix("---") { return "f" }
        return line.first
    }

    private var foreground: Color {
        switch kind {
        case "@": .cyan
        case "f": .secondary
        default: .primary
        }
    }

    private var background: Color {
        switch kind {
        case "+": .green.opacity(0.16)
        case "-": .red.opacity(0.16)
        case "@": .cyan.opacity(0.08)
        default: .clear
        }
    }
}
