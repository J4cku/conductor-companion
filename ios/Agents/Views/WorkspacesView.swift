import SwiftUI

/// Every non-archived workspace, grouped by repository like Conductor's sidebar.
struct WorkspacesView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("collapsedRepos") private var collapsedStore = ""

    private var collapsed: Set<String> { Set(collapsedStore.split(separator: ",").map(String.init)) }

    var body: some View {
        NavigationStack {
            Group {
                if let workspaces = model.workspaces {
                    List {
                        ForEach(workspaces.repos) { repo in
                            Section(isExpanded: expanded(repo.id)) {
                                ForEach(repo.workspaces) { workspace in row(workspace) }
                            } header: {
                                HStack {
                                    Text(repo.name)
                                    Spacer()
                                    let active = repo.workspaces.filter { $0.state != .idle }.count
                                    Text(active > 0 ? "\(active) active · \(repo.workspaces.count)" : "\(repo.workspaces.count)")
                                        .font(.caption).foregroundStyle(.secondary).textCase(nil)
                                }
                            }
                        }
                    }
                    .listStyle(.sidebar)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Workspaces")
            .navigationDestination(for: String.self) { ChatView(sessionID: $0) }
            .refreshable { await model.refresh(workspaces: true) }
            .task {
                while !Task.isCancelled {
                    await model.refresh(workspaces: true)
                    try? await Task.sleep(for: .seconds(5))
                }
            }
        }
    }

    @ViewBuilder private func row(_ workspace: Workspace) -> some View {
        let label = HStack(spacing: 12) {
            StateIcon(state: workspace.state, size: 24)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(workspace.title).font(.body.weight(.medium)).lineLimit(1)
                    if workspace.pinned { Image(systemName: "pin.fill").font(.caption2).foregroundStyle(.secondary) }
                    if workspace.held { Badge(text: "asks", prominent: true) }
                }
                Text(subtitle(workspace)).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            Text(workspace.updated > 0 ? ago(workspace.updated) : "").font(.caption).foregroundStyle(.tertiary)
        }
        if let session = workspace.session {
            NavigationLink(value: session) { label }
        } else {
            label.opacity(0.5)
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
