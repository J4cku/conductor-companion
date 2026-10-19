import SwiftUI

/// One chat in a list: theme icon, name, what it's doing, how long.
struct SessionRow: View {
    @Environment(AppModel.self) private var model
    let session: Session

    var body: some View {
        HStack(spacing: 14) {
            StateIcon(state: session.asking ? .waiting : session.state, size: 30)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(session.name).font(.body.weight(.semibold)).lineLimit(1)
                    if session.asking { Badge(text: "asks", tint: .orange) }
                    if session.queued { Badge(text: "queued") }
                }
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    Text(ago(session.since, now: context.date)).font(.caption.monospacedDigit()).foregroundStyle(.tertiary)
                }
                if let cost = money(session.cost) { Text(cost).font(.caption2.monospacedDigit()).foregroundStyle(.tertiary) }
            }
        }
        .padding(.vertical, 4)
        .contentShape(.rect)
    }

    private var subtitle: String {
        if let question = session.held?.question { return question.question }
        return [session.placeLabel, session.unit, session.activity].compactMap { $0 }.joined(separator: " · ")
    }
}

/// Context-window fill: the theme's food bar.
struct ContextMeter: View {
    let percent: Int

    var body: some View {
        Gauge(value: Double(percent), in: 0...100) {
            Text("Context")
        } currentValueLabel: {
            Text("\(percent)%")
        }
        .gaugeStyle(.accessoryCircularCapacity)
        .tint(percent > 80 ? .red : percent > 50 ? .orange : .green)
        .scaleEffect(0.55)
        .frame(width: 30, height: 30)
    }
}
