import SwiftUI

@main
struct AgentsApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView().environment(model)
        }
    }
}

struct RootView: View {
    enum TabID: Hashable { case active, workspaces, mac }

    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    @State private var tab: TabID = .active
    @State private var activePath: [String] = []
    @State private var linkProblem: String?

    var body: some View {
        Group {
            if model.mac == nil {
                PairingView()
            } else {
                tabs
            }
        }
        .onOpenURL { url in
            Task { linkProblem = await model.open(url) }
        }
        #if DEBUG
        .task {  // simulator testing: `simctl launch … -openURL <link>` skips iOS's "Open in Agents?" prompt
            let arguments = ProcessInfo.processInfo.arguments
            if let index = arguments.firstIndex(of: "-openURL"), index + 1 < arguments.count, let url = URL(string: arguments[index + 1]) {
                linkProblem = await model.open(url)
            }
            if let index = arguments.firstIndex(of: "-tab"), index + 1 < arguments.count {
                tab = ["workspaces": .workspaces, "mac": .mac][arguments[index + 1]] ?? .active
            }
        }
        #endif
        .onChange(of: model.pendingSession) { _, session in
            guard let session else { return }
            tab = .active
            activePath = [session]
            model.pendingSession = nil
        }
        .alert("Couldn't open that link", isPresented: Binding(get: { linkProblem != nil }, set: { if !$0 { linkProblem = nil } })) {
            Button("OK") {}
        } message: {
            Text(linkProblem ?? "")
        }
        .task(id: [phase == .active ? "on" : "off", model.mac?.id ?? ""]) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    private var tabs: some View {
        TabView(selection: $tab) {
            Tab("Active", systemImage: "bolt.horizontal.circle.fill", value: .active) {
                ActiveView(path: $activePath)
            }
            .badge(model.needsYou)
            Tab("Workspaces", systemImage: "square.stack.3d.up.fill", value: .workspaces) {
                WorkspacesView()
            }
            Tab("Mac", systemImage: "laptopcomputer", value: .mac) {
                MacView()
            }
        }
        .tabBarMinimizeBehavior(.onScrollDown)
        .tabViewBottomAccessory(isEnabled: model.snapshot?.away == true) {
            Label("Away — agents' questions come here", systemImage: "figure.walk.departure")
                .font(.footnote.weight(.medium))
        }
    }
}
