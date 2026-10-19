import SwiftUI
import UIKit

extension Color {
    /// `#RRGGBB` from the Mac's theme; the app's yellow when missing or malformed.
    init(hex: String?) {
        guard let hex, hex.hasPrefix("#"), hex.count == 7, let value = UInt32(hex.dropFirst(), radix: 16) else {
            self = Color(red: 1, green: 0.8, blue: 0.02)
            return
        }
        self = Color(red: Double((value >> 16) & 0xFF) / 255, green: Double((value >> 8) & 0xFF) / 255, blue: Double(value & 0xFF) / 255)
    }
}

extension Theme {
    var accentColor: Color { Color(hex: accent) }

    /// The theme's pixel icons, decoded at 2x so 16 px art lands on 8 pt and scales crisply.
    func image(_ state: AgentState) -> UIImage? {
        let key = state == .idle ? AgentState.quiet.rawValue : state.rawValue
        return (icons[key] ?? icons[AgentState.quiet.rawValue]).flatMap(Self.decode)
    }

    var frameImages: [UIImage] { (frames ?? []).compactMap(Self.decode) }

    private static func decode(_ base64: String) -> UIImage? {
        Data(base64Encoded: base64).flatMap { UIImage(data: $0, scale: 2) }
    }
}

func money(_ value: Double?) -> String? {
    guard let value, value > 0 else { return nil }
    return value.formatted(.currency(code: "USD").precision(.fractionLength(value < 10 ? 2 : 0)).locale(Locale(identifier: "en_US")))
}

func ago(_ since: Double, now: Date = .now) -> String {
    let minutes = Int(now.timeIntervalSince1970 - since) / 60
    switch minutes {
    case ..<1: return "now"
    case ..<60: return "\(minutes)m"
    case ..<1440: return "\(minutes / 60)h"
    default: return "\(minutes / 1440)d"
    }
}
