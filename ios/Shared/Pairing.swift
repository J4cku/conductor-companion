import Foundation
import Security

/// A Mac this phone is paired with. The token lives in the Keychain with the rest of the record.
struct PairedMac: Codable, Sendable, Identifiable, Hashable {
    var id: String { base.absoluteString }
    let name: String
    let base: URL
    let token: String
    var pairedAt: Date = .now

    var client: APIClient { APIClient(base: base, token: token) }
}

/// What a pairing link carries: the Mac's address and, from a QR code, a one-time secret.
struct PairingLink: Sendable, Equatable {
    let base: URL
    let secret: String?

    init(base: URL) {
        self.base = base
        secret = nil
    }

    /// Accepts the QR code's web link (`http://192.168.1.10:47823/#pair=SECRET`),
    /// `agents://pair?base=…&secret=…`, or a bare address typed by hand.
    init?(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = URL(string: trimmed), url.scheme == "agents", url.host() == "pair",
           let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
           let base = items.first(where: { $0.name == "base" })?.value.flatMap(URL.init(string:)) {
            self.base = base
            secret = items.first(where: { $0.name == "secret" })?.value
            return
        }
        let candidate = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
        guard var components = URLComponents(string: candidate), let scheme = components.scheme, ["http", "https"].contains(scheme),
              components.host?.isEmpty == false else { return nil }
        secret = components.fragment.flatMap { fragment in
            URLComponents(string: "?\(fragment)")?.queryItems?.first(where: { $0.name == "pair" })?.value
        }
        components.fragment = nil
        components.path = "/"
        components.query = nil
        guard let base = components.url else { return nil }
        self.base = base
    }
}

/// Paired Macs, one Keychain item in the access group the app and its widgets share
/// (the first entry of `keychain-access-groups`, so no group needs naming here).
enum MacKeychain {
    private static let service = "com.j4cku.agents"
    private static let account = "paired-macs"

    static func load() -> [PairedMac] {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return [] }
        return (try? JSONDecoder().decode([PairedMac].self, from: data)) ?? []
    }

    static func save(_ macs: [PairedMac]) {
        guard let data = try? JSONEncoder().encode(macs) else { return }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
        let attributes: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock]
        if SecItemUpdate(query as CFDictionary, attributes as CFDictionary) == errSecItemNotFound {
            SecItemAdd(query.merging(attributes) { $1 } as CFDictionary, nil)
        }
    }
}

/// State the app shares with its widgets and Live Activity through the app group.
enum SharedStore {
    static let group = "group.com.j4cku.agents"
    nonisolated(unsafe) static let defaults = UserDefaults(suiteName: group) ?? .standard  // UserDefaults is thread-safe

    static var selectedMacID: String? {
        get { defaults.string(forKey: "selectedMac") }
        set { defaults.set(newValue, forKey: "selectedMac") }
    }

    /// The Mac the app shows, for the widgets too.
    static var currentMac: PairedMac? {
        let macs = MacKeychain.load()
        return macs.first { $0.id == selectedMacID } ?? macs.first
    }

    /// The last snapshot, so launch and widgets have something to show before the Mac answers.
    static var lastSnapshot: SessionsSnapshot? {
        get { defaults.data(forKey: "snapshot").flatMap { try? JSONDecoder().decode(SessionsSnapshot.self, from: $0) } }
        set { defaults.set(newValue.flatMap { try? JSONEncoder().encode($0) }, forKey: "snapshot") }
    }
}
