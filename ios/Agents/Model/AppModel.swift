import Foundation
import Observation
import UIKit

/// App-wide state: which Macs are paired, which one is shown, and its latest snapshot.
@MainActor @Observable
final class AppModel {
    private(set) var macs: [PairedMac] = MacKeychain.load()
    var selectedMacID: String? = UserDefaults.standard.string(forKey: "selectedMac")
    private(set) var snapshot: SessionsSnapshot?
    private(set) var workspaces: WorkspacesSnapshot?
    private(set) var problem: String?
    private(set) var icons: [String: UIImage] = [:]
    /// A chat to open, from a link or a notification.
    var pendingSession: String?

    var mac: PairedMac? { macs.first { $0.id == selectedMacID } ?? macs.first }
    var client: APIClient? { mac.map { APIClient(base: $0.base, token: $0.token) } }
    var needsYou: Int { snapshot?.sessions.filter { $0.held != nil || $0.state == .waiting || $0.state == .failed }.count ?? 0 }

    // MARK: Live data

    func refresh(workspaces includeWorkspaces: Bool = false) async {
        guard let client else { return }
        do {
            let next = try await client.sessions()
            if next != snapshot {
                if next.theme != snapshot?.theme { icons = Self.decodeIcons(next.theme) }
                snapshot = next
            }
            if includeWorkspaces {
                let list = try await client.workspaces()
                if list != workspaces { workspaces = list }
            }
            problem = nil
        } catch let error as ServerError where error.status == 401 {
            problem = "This Mac no longer recognises the phone. Pair again."
        } catch {
            problem = "Can't reach \(mac?.name ?? "the Mac"): \(error.localizedDescription)"
        }
    }

    func icon(_ state: AgentState) -> UIImage? {
        icons[state == .idle ? AgentState.quiet.rawValue : state.rawValue] ?? icons[AgentState.quiet.rawValue]
    }

    private static func decodeIcons(_ theme: Theme) -> [String: UIImage] {
        theme.icons.compactMapValues { encoded in
            Data(base64Encoded: encoded).flatMap { UIImage(data: $0, scale: 2) }
        }
    }

    func setAway(_ on: Bool) async {
        guard let client else { return }
        do {
            _ = try await client.setAway(on)
            await refresh()
        } catch {
            problem = error.localizedDescription
        }
    }

    func send(_ text: String, to session: String) async throws -> ReplyResult {
        guard let client else { throw ServerError(message: "No Mac paired.", status: 0) }
        return try await client.reply(session, text: text)
    }

    // MARK: Pairing

    func pair(_ link: PairingLink, code: String?) async throws {
        let device = UIDevice.current.name
        let result = try await APIClient(base: link.base, token: nil).pair(secret: link.secret, code: code, device: device)
        let mac = PairedMac(name: result.computer, base: link.base, token: result.token)
        macs.removeAll { $0.id == mac.id }
        macs.append(mac)
        MacKeychain.save(macs)
        select(mac)
    }

    func select(_ mac: PairedMac) {
        selectedMacID = mac.id
        UserDefaults.standard.set(mac.id, forKey: "selectedMac")
        snapshot = nil
        workspaces = nil
        problem = nil
    }

    func forget(_ mac: PairedMac) {
        macs.removeAll { $0.id == mac.id }
        MacKeychain.save(macs)
        if selectedMacID == mac.id {
            selectedMacID = nil
            snapshot = nil
            workspaces = nil
        }
    }

    /// `agents://session/<id>` opens a chat; pairing links pair.
    func open(_ url: URL) async -> String? {
        if url.scheme == "agents", url.host() == "session" {
            pendingSession = url.pathComponents.dropFirst().first
            return nil
        }
        guard let link = PairingLink(url.absoluteString), link.secret != nil else { return "That link isn't a pairing code." }
        do {
            try await pair(link, code: nil)
            return nil
        } catch {
            return error.localizedDescription
        }
    }
}
