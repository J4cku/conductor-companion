import SwiftUI

/// The theme's pixel-art state icon, sent by the Mac.
struct StateIcon: View {
    @Environment(AppModel.self) private var model
    let state: AgentState
    var size: CGFloat = 28

    var body: some View {
        Group {
            if let image = model.icon(state) {
                Image(uiImage: image).resizable().interpolation(.none)
            } else {
                Image(systemName: "circle.dotted").resizable().foregroundStyle(.tertiary)
            }
        }
        .frame(width: size, height: size)
        .opacity(state == .idle ? 0.35 : 1)
        .grayscale(state == .idle ? 1 : 0)
        .accessibilityLabel(model.snapshot?.theme.label(state) ?? state.rawValue)
    }
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

struct SessionRow: View {
    let session: Session

    var body: some View {
        HStack(spacing: 12) {
            StateIcon(state: session.state)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(session.name).font(.body.weight(.semibold)).lineLimit(1)
                    if session.queued { Badge(text: "queued") }
                }
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            TimelineView(.periodic(from: .now, by: 30)) { context in
                Text(ago(session.since, now: context.date)).font(.caption).foregroundStyle(.tertiary).monospacedDigit()
            }
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        if let question = session.held?.question { return "Asks: \(question.question)" }
        return [session.placeLabel, session.unit, session.doing?.isEmpty == false ? session.doing : session.quote].compactMap { $0 }.joined(separator: " · ")
    }
}

struct Badge: View {
    let text: String
    var prominent = false

    var body: some View {
        Text(text).font(.caption2.weight(.semibold)).padding(.horizontal, 7).padding(.vertical, 2)
            .foregroundStyle(prominent ? Color.black : Color.secondary)
            .background(prominent ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.fill.tertiary), in: .capsule)
    }
}

/// Shown when the Mac can't be reached or the phone was unpaired.
struct ProblemBanner: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "wifi.exclamationmark")
            .font(.footnote).foregroundStyle(.secondary)
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassEffect(.regular, in: .rect(cornerRadius: 16))
            .padding(.horizontal)
    }
}
