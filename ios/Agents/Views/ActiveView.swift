import SwiftUI

/// Sessions that are doing something or need you, most urgent first.
struct ActiveView: View {
    @Environment(AppModel.self) private var model
    @Binding var path: [String]

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if let snapshot = model.snapshot {
                    list(snapshot)
                } else if let problem = model.problem {
                    ContentUnavailableView("Can't reach your Mac", systemImage: "laptopcomputer.slash", description: Text(problem))
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Agents")
            .navigationSubtitle(model.snapshot?.computer ?? model.mac?.name ?? "")
            .navigationDestination(for: String.self) { ChatView(sessionID: $0) }
            .refreshable { await model.refresh() }
        }
    }

    private func list(_ snapshot: SessionsSnapshot) -> some View {
        let asking = snapshot.sessions.filter { $0.held != nil }
        return List {
            if let problem = model.problem {
                ProblemBanner(text: problem).listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
            }
            if !asking.isEmpty {
                Section("Waiting for your answer") {
                    ForEach(asking) { session in
                        NavigationLink(value: session.id) { SessionRow(session: session) }
                            .listRowBackground(Color.accentColor.opacity(0.12))
                    }
                }
            }
            ForEach(AgentState.attention, id: \.self) { state in
                let group = snapshot.sessions.filter { $0.state == state && $0.held == nil }
                if !group.isEmpty {
                    Section(snapshot.theme.label(state).capitalized) {
                        ForEach(group) { session in
                            NavigationLink(value: session.id) { SessionRow(session: session) }
                        }
                    }
                }
            }
        }
        .overlay {
            if snapshot.sessions.isEmpty {
                ContentUnavailableView("All quiet", systemImage: "moon.zzz", description: Text("No agent is working or waiting. Start one from the Workspaces tab."))
            }
        }
        .animation(.default, value: snapshot.sessions.map(\.id))
    }
}
