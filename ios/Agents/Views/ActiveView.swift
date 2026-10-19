import SwiftUI

/// What needs you, then what's working, then what finished — with a glance card on top.
struct ActiveView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.horizontalSizeClass) private var size
    @Binding var path: [String]
    @Namespace private var zoom

    var body: some View {
        if size == .regular {
            NavigationSplitView {
                content.navigationSplitViewColumnWidth(min: 340, ideal: 400, max: 480)
            } detail: {
                NavigationStack {
                    if let id = path.last {
                        ChatView(sessionID: id).id(id)
                    } else {
                        ContentUnavailableView("Pick a chat", systemImage: "bubble.left.and.text.bubble.right", description: Text("Its conversation opens here."))
                            .background { AmbientBackground(tint: model.accent, intensity: 0.3) }
                    }
                }
            }
        } else {
            NavigationStack(path: $path) {
                content.navigationDestination(for: String.self) { id in
                    ChatView(sessionID: id).navigationTransition(.zoom(sourceID: id, in: zoom))
                }
            }
        }
    }

    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let problem = model.problem { ProblemBanner(text: problem) }
                if let snapshot = model.snapshot {
                    SummaryCard(snapshot: snapshot)
                    sections(snapshot)
                } else if model.problem == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 120)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 40)
        }
        .background { AmbientBackground(tint: model.accent) }
        .navigationTitle("Agents")
        .navigationSubtitle(subtitle)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if model.live {
                    Image(systemName: "dot.radiowaves.left.and.right").foregroundStyle(model.accent).symbolEffect(.variableColor.iterative)
                        .accessibilityLabel("Live")
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    private var subtitle: String {
        [model.snapshot?.computer ?? model.mac?.name, model.snapshot.map { $0.theme.name }].compactMap { $0 }.joined(separator: " · ")
    }

    @ViewBuilder private func sections(_ snapshot: SessionsSnapshot) -> some View {
        let sessions = model.visible
        let asking = sessions.filter(\.asking)
        if sessions.isEmpty {
            ContentUnavailableView {
                Label("All quiet", systemImage: "moon.zzz.fill")
            } description: {
                Text("No agent is working or waiting. Message one from Workspaces.")
            }
            .padding(.top, 40)
        }
        if !asking.isEmpty { section("Waiting for your answer", asking, tint: .orange) }
        ForEach(AgentState.attention, id: \.self) { state in
            let group = sessions.filter { $0.state == state && !$0.asking }
            if !group.isEmpty { section(snapshot.theme.label(state).capitalized, group, tint: state.needsYou ? .orange : nil) }
        }
    }

    private func section(_ title: String, _ sessions: [Session], tint: Color?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.footnote.weight(.semibold)).textCase(.uppercase).foregroundStyle(.secondary).padding(.leading, 6)
            GlassEffectContainer(spacing: 8) {
                VStack(spacing: 8) {
                    ForEach(sessions) { session in
                        let selected = size == .regular && path.last == session.id
                        Button {
                            path = size == .regular ? [session.id] : path + [session.id]
                        } label: {
                            SessionRow(session: session)
                                .padding(.horizontal, 14).padding(.vertical, 10)
                                .glassCard(selected ? model.accent : tint, radius: 22)
                                .overlay { if selected { RoundedRectangle(cornerRadius: 22).strokeBorder(model.accent.opacity(0.6), lineWidth: 1.5) } }
                                .matchedTransitionSource(id: session.id, in: zoom)
                        }
                        .buttonStyle(.plain)
                        .contextMenu {
                            if session.state != .working { Button("Mark as seen", systemImage: "checkmark.circle") { model.markSeen(session) } }
                            Button("Snooze for an hour", systemImage: "moon.zzz") { model.snooze(session) }
                        }
                        .transition(.asymmetric(insertion: .push(from: .bottom).combined(with: .opacity), removal: .opacity))
                    }
                }
            }
        }
    }
}
