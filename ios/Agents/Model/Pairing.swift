import Foundation
import Security

/// A Mac this phone is paired with. The token lives in the Keychain with the rest of the record.
struct PairedMac: Codable, Sendable, Identifiable, Hashable {
    var id: String { base.absoluteString }
    let name: String
    let base: URL
    let token: String
    var pairedAt: Date = .now
}

/// What a pairing link carries: the Mac's address and, from a QR code, a one-time secret.
struct PairingLink: Sendable, Equatable {
    let base: URL
    let secret: String?

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

/// Paired Macs, stored as one Keychain item.
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
