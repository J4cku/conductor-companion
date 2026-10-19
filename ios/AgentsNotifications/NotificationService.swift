import OSLog
import UserNotifications

/// Turns an agent's multiple-choice question into notification buttons, one per option (up to three), next to
/// a free-text answer. Categories are static app-wide, so each set of options gets its own, registered just
/// before the notification is shown.
final class NotificationService: UNNotificationServiceExtension {
    private static let prefix = "AGENT_QUESTION_"

    override func didReceive(_ request: UNNotificationRequest, withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        let content = (request.content.mutableCopy() as? UNMutableNotificationContent) ?? UNMutableNotificationContent()
        let options = Array((request.content.userInfo["options"] as? [String] ?? []).prefix(3))
        guard !options.isEmpty else { return contentHandler(content) }
        let identifier = Self.prefix + Self.digest(options.joined(separator: "\u{1F}"))
        nonisolated(unsafe) let deliver = contentHandler
        nonisolated(unsafe) let mutable = content
        Task {
            let actions = options.enumerated().map { UNNotificationAction(identifier: "OPTION_\($0.offset)", title: $0.element) }
                + [UNTextInputNotificationAction(identifier: "REPLY", title: "Other answer…", textInputButtonTitle: "Send", textInputPlaceholder: "Your answer")]
            let category = UNNotificationCategory(identifier: identifier, actions: actions, intentIdentifiers: [])
            let center = UNUserNotificationCenter.current()
            var categories = await center.notificationCategories()
            if categories.count > 40 { categories = categories.filter { !$0.identifier.hasPrefix(Self.prefix) } }
            categories.insert(category)
            center.setNotificationCategories(categories)
            try? await Task.sleep(for: .milliseconds(150))  // registration is asynchronous
            mutable.categoryIdentifier = identifier
            Logger(subsystem: "com.j4cku.agents", category: "notifications").info("question with \(options.count) options as \(identifier, privacy: .public)")
            deliver(mutable)
        }
    }

    /// FNV-1a: stable across launches, unlike `hashValue`.
    private static func digest(_ text: String) -> String {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in text.utf8 { hash = (hash ^ UInt64(byte)) &* 0x100_0000_01b3 }
        return String(hash, radix: 16)
    }
}
