import ActivityKit
import Foundation
import LocalAuthentication
import Observation
import OSLog
import SwiftUI
import UIKit
import UserNotifications
import WidgetKit

/// App-wide state: which Macs are paired, which one is shown, its latest snapshot, and the phone-side extras
/// (Live Activity, push registration, Face ID lock, what you've marked seen or snoozed).
@MainActor @Observable
final class AppModel {
    private(set) var macs: [PairedMac] = MacKeychain.load()
    private(set) var snapshot: SessionsSnapshot? = SharedStore.lastSnapshot
    private(set) var workspaces: WorkspacesSnapshot?
    private(set) var problem: String?
    private(set) var live = false
    /// A chat to open, from a link, a notification, Siri or a widget.
    var pendingSession: String?
    var locked = (UserDefaults.standard.object(forKey: "requireFaceID") as? Bool ?? true) && !ProcessInfo.processInfo.arguments.contains("-unlocked")
    var requireFaceID = UserDefaults.standard.object(forKey: "requireFaceID") as? Bool ?? true {
        didSet { UserDefaults.standard.set(requireFaceID, forKey: "requireFaceID") }
    }
    private var seen: [String: Double] = UserDefaults.standard.dictionary(forKey: "seen") as? [String: Double] ?? [:]
    private var snoozed: [String: Double] = UserDefaults.standard.dictionary(forKey: "snoozed") as? [String: Double] ?? [:]
    private var pushToken: String?
    private var backgroundedAt: Date?
    private var lastWidgetKey = ""

    var mac: PairedMac? { macs.first { $0.id == SharedStore.selectedMacID } ?? macs.first }
    var client: APIClient? { mac?.client }
    var accent: Color { snapshot?.theme.accentColor ?? Color(hex: nil) }
    var needsYou: Int { visible.filter { $0.asking || $0.state.needsYou }.count }

    /// Sessions minus the ones you've marked seen (until their state changes) or snoozed.
    var visible: [Session] {
        let now = Date.now.timeIntervalSince1970
        return (snapshot?.sessions ?? []).filter { session in
            if let until = snoozed[session.id], until > now, session.state != .working || session.asking { return false }
            if let at = seen[session.id], at >= session.since, !session.asking, session.state != .working { return false }
            return true
        }
    }

    // MARK: Live data

    /// Follows the Mac's live stream; falls back to polling when the stream isn't available.
    func follow() async {
        while !Task.isCancelled {
            guard let client else { return }
            do {
                for try await (event, payload) in client.events() where event == "snapshot" {
                    live = true
                    if let next = try? JSONDecoder().decode(SessionsSnapshot.self, from: payload) { apply(next) }
                }
            } catch {}
            live = false
            await refresh()
            try? await Task.sleep(for: .seconds(3))
        }
    }

    func refresh() async {
        guard let client else { return }
        do {
            apply(try await client.sessions())
            problem = nil
        } catch let error as ServerError where error.status == 401 {
            problem = "This Mac no longer recognises the phone. Pair again."
        } catch {
            problem = "Can't reach \(mac?.name ?? "the Mac"): \(error.localizedDescription)"
        }
    }

    /// The workspace list; sessions come from the live stream.
    func refreshWorkspaces() async {
        guard let client else { return }
        do {
            let list = try await client.workspaces()
            if list != workspaces { withAnimation(.snappy) { workspaces = list } }
        } catch let error as ServerError where error.status == 401 {
            problem = "This Mac no longer recognises the phone. Pair again."
        } catch {
            problem = "Can't reach \(mac?.name ?? "the Mac"): \(error.localizedDescription)"
        }
    }

    private func apply(_ next: SessionsSnapshot) {
        problem = nil
        guard next != snapshot else { return }
        withAnimation(.snappy) { snapshot = next }
        SharedStore.lastSnapshot = next
        SharedStore.defaults.set(next.theme.accent, forKey: "accent")
        updateLiveActivity(next)
        let key = "\(next.working.count)|\(next.needingYou.count)|\(next.theme.name)"
        if key != lastWidgetKey {
            lastWidgetKey = key
            WidgetCenter.shared.reloadAllTimelines()
        }
        UNUserNotificationCenter.current().setBadgeCount(needsYou)
    }

    func setAway(_ on: Bool) async {
        guard let client else { return }
        do {
            _ = try await client.setAway(on)
            await refresh()
            ControlCenter.shared.reloadAllControls()
        } catch {
            problem = error.localizedDescription
        }
    }

    func send(_ text: String, to session: String) async throws -> ReplyResult {
        guard let client else { throw ServerError(message: "No Mac paired.", status: 0) }
        return try await client.reply(session, text: text)
    }

    // MARK: Seen and snoozed

    func markSeen(_ session: Session) {
        seen[session.id] = session.since
        UserDefaults.standard.set(seen, forKey: "seen")
    }

    func snooze(_ session: Session, for interval: TimeInterval = 3600) {
        snoozed[session.id] = Date.now.timeIntervalSince1970 + interval
        UserDefaults.standard.set(snoozed, forKey: "snoozed")
    }

    // MARK: Live Activity

    private func updateLiveActivity(_ snapshot: SessionsSnapshot) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            log.info("Live Activities are off for this app")
            return
        }
        let state = AgentsActivityAttributes.ContentState(snapshot)
        let busy = state.working > 0 || state.needsYou > 0
        Task {
            if let current = Activity<AgentsActivityAttributes>.activities.first {
                if busy {
                    await current.update(ActivityContent(state: state, staleDate: .now.addingTimeInterval(600)))
                } else {
                    await current.end(ActivityContent(state: state, staleDate: nil), dismissalPolicy: .after(.now.addingTimeInterval(60)))
                }
            } else if busy, UIApplication.shared.applicationState == .active {
                let attributes = AgentsActivityAttributes(computer: snapshot.computer)
                do {
                    let activity = try Activity.request(attributes: attributes, content: ActivityContent(state: state, staleDate: nil), pushType: .token)
                    observePushToken(of: activity)
                } catch {
                    log.error("Live Activity request failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    private func observePushToken(of activity: Activity<AgentsActivityAttributes>) {
        Task {
            for await token in activity.pushTokenUpdates {
                await register(push: token.hex, kind: "liveactivity")
            }
        }
    }

    /// Lets the Mac start the Live Activity itself when an agent gets busy while the app is closed.
    func observePushToStart() {
        Task {
            for await token in Activity<AgentsActivityAttributes>.pushToStartTokenUpdates {
                await register(push: token.hex, kind: "pushtostart")
            }
        }
        Task {  // includes activities the Mac starts by push while the app is closed
            for await activity in Activity<AgentsActivityAttributes>.activityUpdates { observePushToken(of: activity) }
        }
    }

    // MARK: Push notifications

    func enableNotifications() async {
        let center = UNUserNotificationCenter.current()
        guard (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) == true else { return }
        UIApplication.shared.registerForRemoteNotifications()
    }

    func didRegister(deviceToken: Data) {
        pushToken = deviceToken.hex
        Task { await register(push: deviceToken.hex, kind: "alert") }
    }

    private func register(push token: String, kind: String) async {
        #if DEBUG
        let environment = "development"
        #else
        let environment = "production"
        #endif
        try? await client?.registerPush(token: token, kind: kind, environment: environment)
    }

    // MARK: Face ID

    func backgrounded() {
        backgroundedAt = .now
    }

    /// Locks after a minute away, then asks for Face ID straight away.
    func foregrounded() async {
        if requireFaceID, let at = backgroundedAt, Date.now.timeIntervalSince(at) > 60 { locked = true }
        backgroundedAt = nil
        if locked { await unlock() }
    }

    func unlock() async {
        guard locked else { return }
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            locked = false  // no passcode or biometrics set up: nothing to unlock with
            return
        }
        if (try? await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock Agents")) == true {
            withAnimation(.smooth) { locked = false }
        }
    }

    // MARK: Pairing

    func pair(_ link: PairingLink, code: String?) async throws {
        let result = try await APIClient(base: link.base, token: nil).pair(secret: link.secret, code: code, device: UIDevice.current.name)
        let mac = PairedMac(name: result.computer, base: link.base, token: result.token)
        macs.removeAll { $0.id == mac.id }
        macs.append(mac)
        MacKeychain.save(macs)
        locked = false
        select(mac)
        if let pushToken { await register(push: pushToken, kind: "alert") }
    }

    func select(_ mac: PairedMac) {
        SharedStore.selectedMacID = mac.id
        snapshot = nil
        workspaces = nil
        problem = nil
        WidgetCenter.shared.reloadAllTimelines()
    }

    func forget(_ mac: PairedMac) {
        macs.removeAll { $0.id == mac.id }
        MacKeychain.save(macs)
        if SharedStore.selectedMacID == mac.id {
            SharedStore.selectedMacID = nil
            snapshot = nil
            workspaces = nil
            SharedStore.lastSnapshot = nil
        }
    }

    /// `agents://session/<id>` opens a chat; pairing links pair.
    func open(_ url: URL) async -> String? {
        if url.scheme == "agents", url.host() == "session" {
            pendingSession = url.pathComponents.dropFirst().first
            return nil
        }
        if url.scheme == "agents", url.host() != "pair" { return nil }
        guard let link = PairingLink(url.absoluteString), link.secret != nil else { return "That link isn't a pairing code." }
        do {
            try await pair(link, code: nil)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    /// A chat Siri or a widget asked to open while the app was closed.
    func takePendingFromIntent() {
        if let id = SharedStore.defaults.string(forKey: "pendingSession") {
            SharedStore.defaults.removeObject(forKey: "pendingSession")
            pendingSession = id
        }
    }
}

private let log = Logger(subsystem: "com.j4cku.agents", category: "app")

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
