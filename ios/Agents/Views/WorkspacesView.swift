import SwiftUI

/// Every non-archived workspace, grouped by repository like Conductor's sidebar.
struct WorkspacesView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("collapsedRepos") private var collapsedStore = ""
    @Environment(\.horizontalSizeClass) private var size
    @State private var newTask = false
    @State private var selected: String?
    @Namespace private var zoom

    private var collapsed: Set<String> { Set(collapsedStore.split(separator: ",").map(String.init)) }

    var body: some View {
        if size == .regular {
            NavigationSplitView {
                list.navigationSplitViewColumnWidth(min: 340, ideal: 400, max: 480)
            } detail: {
                NavigationStack {
                    if let selected {
                        ChatView(sessionID: selected).id(selected)
                    } else {
                        ContentUnavailableView("Pick a workspace", systemImage: "square.stack.3d.up", description: Text("Its chat opens here."))
                            .background { AmbientBackground(tint: model.accent, intensity: 0.3) }
                    }
                }
            }
        } else {
            NavigationStack {
                list.navigationDestination(for: String.self) { id in
                    ChatView(sessionID: id).navigationTransition(.zoom(sourceID: id, in: zoom))
                }
            }
        }
    }

    private var list: some View {
        Group {
            if let workspaces = model.workspaces {
                List(selection: size == .regular ? $selected : nil) {
                    ForEach(workspaces.repos) { repo in
                        Section(isExpanded: expanded(repo.id)) {
                            ForEach(repo.workspaces) { workspace in row(workspace) }
                        } header: {
                            HStack {
                                Text(repo.name)
                                Spacer()
                                let active = repo.workspaces.filter { $0.state != .idle }.count
                                if active > 0 { Badge(text: "\(active) active", tint: model.accent) }
                                Text("\(repo.workspaces.count)").font(.caption).foregroundStyle(.secondary).textCase(nil)
                            }
                        }
                    }
                }
                .listStyle(.sidebar)
                .scrollContentBackground(.hidden)
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { AmbientBackground(tint: model.accent, intensity: 0.6) }
        .navigationTitle("Workspaces")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("New task", systemImage: "plus") { newTask = true }.disabled(model.workspaces == nil)
            }
        }
        .sheet(isPresented: $newTask) { NewTaskView(repos: model.workspaces?.repos ?? []) }
        .refreshable { await model.refreshWorkspaces() }
        .task {
            while !Task.isCancelled {
                await model.refreshWorkspaces()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    @ViewBuilder private func row(_ workspace: Workspace) -> some View {
        let label = HStack(spacing: 12) {
            StateIcon(state: workspace.held ? .waiting : workspace.state, size: 24)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(workspace.title).font(.body.weight(.medium)).lineLimit(1)
                    if workspace.pinned { Image(systemName: "pin.fill").font(.caption2).foregroundStyle(.secondary) }
                    if workspace.held { Badge(text: "asks", tint: .orange) }
                }
                Text(subtitle(workspace)).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            Text(workspace.updated > 0 ? ago(workspace.updated) : "").font(.caption.monospacedDigit()).foregroundStyle(.tertiary)
        }
        if let session = workspace.session {
            NavigationLink(value: session) { label.matchedTransitionSource(id: session, in: zoom) }
                .listRowBackground(Color.clear)
        } else {
            label.opacity(0.5).listRowBackground(Color.clear)
        }
    }

    private func subtitle(_ workspace: Workspace) -> String {
        let status = ["in-progress": "In progress", "in-review": "In review", "done": "Done", "canceled": "Canceled"][workspace.status]
        return [workspace.title == workspace.directory ? nil : workspace.directory, status, workspace.chats > 1 ? "\(workspace.chats) chats" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func expanded(_ id: String) -> Binding<Bool> {
        Binding {
            !collapsed.contains(id)
        } set: { open in
            var set = collapsed
            if open { set.remove(id) } else { set.insert(id) }
            collapsedStore = set.sorted().joined(separator: ",")
        }
    }
}

/// Starts a new Conductor workspace on the Mac from a prompt (opens Conductor's composer there).
struct NewTaskView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let repos: [Repository]
    @State private var repo = ""
    @State private var prompt = ""
    @State private var working = false
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                Picker("Repository", selection: $repo) {
                    ForEach(repos) { Text($0.name).tag($0.id) }
                }
                Section {
                    TextField("What should the agent do?", text: $prompt, axis: .vertical).lineLimit(4...12)
                } footer: {
                    Text(problem ?? "Opens Conductor on your Mac with a new workspace and this prompt ready to go.")
                        .foregroundStyle(problem == nil ? Color.secondary : Color.red)
                }
            }
            .navigationTitle("New task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(working ? "Starting…" : "Start") { start() }
                        .disabled(working || repo.isEmpty || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear { if repo.isEmpty { repo = repos.first?.id ?? "" } }
        }
        .presentationDetents([.medium, .large])
    }

    private func start() {
        working = true
        Task {
            defer { working = false }
            do {
                try await model.client?.startTask(repo: repo, prompt: prompt)
                dismiss()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}
