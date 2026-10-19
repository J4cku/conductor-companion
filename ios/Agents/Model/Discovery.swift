import Foundation
import Network
import Observation

/// Macs advertising the agent-status server on this network (Bonjour `_agentstatus._tcp`).
/// The server puts its own address in the TXT record, so no resolving is needed.
@MainActor @Observable
final class MacDiscovery {
    struct Found: Identifiable, Hashable {
        var id: String { name }
        let name: String
        let base: URL
    }

    private(set) var found: [Found] = []
    private var browser: NWBrowser?

    func start() {
        guard browser == nil else { return }
        let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_agentstatus._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let found: [Found] = results.compactMap { result in
                guard case let .service(name, _, _, _) = result.endpoint,
                      case let .bonjour(txt) = result.metadata,
                      let url = txt["url"].flatMap(URL.init(string:)) else { return nil }
                return Found(name: name, base: url)
            }
            Task { @MainActor in self?.found = found.sorted { $0.name < $1.name } }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    func stop() {
        browser?.cancel()
        browser = nil
    }
}
