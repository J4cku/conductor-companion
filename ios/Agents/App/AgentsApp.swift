import AppIntents
import OSLog
import SwiftUI
import UIKit
import UserNotifications

@main
struct AgentsApp: App {
    @UIApplicationDelegateAdaptor private var delegate: AppDelegate
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .tint(model.accent)
                .onAppear { delegate.model = model }
        }
    }
}

/// Push registration, and notification actions: reply from the lock screen, or tap to open the chat.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    var model: AppModel?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let open = UNNotificationAction(identifier: "OPEN", title: "Open", options: [.foreground])
        let reply = UNTextInputNotificationAction(identifier: "REPLY", title: "Reply", textInputButtonTitle: "Send", textInputPlaceholder: "Message the agent")
        let answer = UNTextInputNotificationAction(identifier: "REPLY", title: "Answer", textInputButtonTitle: "Send", textInputPlaceholder: "Your answer")
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "AGENT_REPLY", actions: [reply, open], intentIdentifiers: []),
            UNNotificationCategory(identifier: "AGENT_QUESTION", actions: [answer, open], intentIdentifiers: []),
        ])
        Task { @MainActor in
            if [.authorized, .provisional, .ephemeral].contains(await center.notificationSettings().authorizationStatus) {
                application.registerForRemoteNotifications()
            }
        }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        model?.didRegister(deviceToken: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Logger(subsystem: "com.j4cku.agents", category: "push").error("No push token: \(error.localizedDescription, privacy: .public)")
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        guard let session = info["sessionId"] as? String else { return }
        var answer = (response as? UNTextInputNotificationResponse)?.userText.trimmingCharacters(in: .whitespacesAndNewlines)
        if response.actionIdentifier.hasPrefix("OPTION_"), let index = Int(response.actionIdentifier.dropFirst(7)),
           let options = info["options"] as? [String], options.indices.contains(index) {
            answer = options[index]
        }
        if let answer, !answer.isEmpty {
            _ = try? await SharedStore.currentMac?.client.reply(session, text: answer)
            return
        }
        await MainActor.run { model?.pendingSession = session }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
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
        .overlay {
            if model.mac != nil, model.locked || (phase != .active && model.requireFaceID) {
                LockView().transition(.opacity)
            }
        }
        .animation(.smooth, value: model.locked)
        .sensoryFeedback(trigger: model.needsYou) { before, after in after > before ? .warning : nil }
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
            if arguments.contains("-unlocked") { model.locked = false }
            if arguments.contains("-quietNotifications") {  // provisional: no prompt, so simctl push works unattended
                _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge, .provisional])
                UIApplication.shared.registerForRemoteNotifications()
            }
            if let index = arguments.firstIndex(of: "-session"), index + 1 < arguments.count {
                model.pendingSession = arguments[index + 1]
            }
        }
        #endif
        .task { model.observePushToStart() }
        .onChange(of: model.pendingSession, initial: true) { _, session in
            guard let session else { return }
            tab = .active
            activePath = [session]
            model.pendingSession = nil
        }
        .onChange(of: phase, initial: true) { _, phase in
            switch phase {
            case .background: model.backgrounded()
            case .active:
                model.takePendingFromIntent()
                Task { await model.foregrounded() }
            default: break
            }
        }
        .alert("Couldn't open that link", isPresented: Binding(get: { linkProblem != nil }, set: { if !$0 { linkProblem = nil } })) {
            Button("OK") {}
        } message: {
            Text(linkProblem ?? "")
        }
        .task(id: [phase == .active ? "on" : "off", model.mac?.id ?? ""]) {
            guard phase == .active else { return }
            await model.follow()
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
        .tabViewStyle(.sidebarAdaptable)
        .tabBarMinimizeBehavior(.onScrollDown)
        .tabViewBottomAccessory(isEnabled: model.snapshot?.away == true) {
            Label("Away — agents' questions come here", systemImage: "figure.walk.departure")
                .font(.footnote.weight(.medium))
        }
    }
}

/// Covers the app until Face ID (or the passcode) unlocks it, and hides it in the app switcher.
struct LockView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial).ignoresSafeArea()
            AmbientBackground(tint: model.accent, intensity: 0.8).opacity(0.7)
            VStack(spacing: 18) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 40, weight: .semibold))
                    .foregroundStyle(model.accent)
                    .frame(width: 96, height: 96)
                    .glassEffect(.regular.tint(model.accent.opacity(0.2)), in: .circle)
                Text("Agents is locked").font(.title3.weight(.semibold))
                if model.locked {
                    Button {
                        Task { await model.unlock() }
                    } label: {
                        Label("Unlock", systemImage: "faceid").padding(.horizontal, 10)
                    }
                    .buttonStyle(.glassProminent)
                    .controlSize(.large)
                }
            }
        }
    }
}

struct AgentsShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: MessageAgentIntent(), phrases: [
            "Message an agent in \(.applicationName)",
            "Tell \(\.$agent) something in \(.applicationName)",
        ], shortTitle: "Message Agent", systemImageName: "bubble.left.and.text.bubble.right.fill")
        AppShortcut(intent: OpenAgentIntent(), phrases: [
            "Open \(\.$agent) in \(.applicationName)",
            "Show my agent in \(.applicationName)",
        ], shortTitle: "Open Agent", systemImageName: "bolt.horizontal.circle.fill")
        AppShortcut(intent: SetAwayIntent(), phrases: [
            "Set away mode in \(.applicationName)",
            "I'm stepping away in \(.applicationName)",
        ], shortTitle: "Away Mode", systemImageName: "figure.walk.departure")
    }
}
