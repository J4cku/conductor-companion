import SwiftUI

/// A slow mesh gradient in the theme's colour behind the app's screens.
struct AmbientBackground: View {
    let tint: Color
    var intensity: Double = 1
    private let base = Color(uiColor: .systemBackground)

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20)) { context in
            let t = context.date.timeIntervalSinceReferenceDate / 7
            MeshGradient(width: 3, height: 3, points: [
                [0, 0], [0.5, 0], [1, 0],
                [0, 0.5], [Float(0.5 + 0.18 * sin(t)), Float(0.42 + 0.12 * cos(t * 1.3))], [1, 0.5],
                [0, 1], [0.5, 1], [1, 1],
            ], colors: [
                tint.opacity(0.42 * intensity), base, tint.opacity(0.16 * intensity),
                base, tint.opacity(0.24 * intensity), base,
                base, tint.opacity(0.1 * intensity), base,
            ])
            .background(base)
        }
        .ignoresSafeArea()
    }
}

/// The theme's pixel-art icon; while working it plays the theme's animation (coin spin, radar ping, Poké Ball wobble).
struct StateIcon: View {
    @Environment(AppModel.self) private var model
    let state: AgentState
    var size: CGFloat = 28

    var body: some View {
        let theme = model.snapshot?.theme
        let frames = state == .working ? theme?.frameImages ?? [] : []
        Group {
            if frames.count > 1 {
                TimelineView(.periodic(from: .now, by: 0.3)) { context in
                    let index = Int(context.date.timeIntervalSinceReferenceDate / 0.3) % frames.count
                    Image(uiImage: frames[index]).resizable().interpolation(.none)
                }
            } else if let image = theme?.image(state) {
                Image(uiImage: image).resizable().interpolation(.none)
            } else {
                Image(systemName: "circle.dotted").resizable().foregroundStyle(.tertiary)
            }
        }
        .frame(width: size, height: size)
        .opacity(state == .idle ? 0.35 : 1)
        .grayscale(state == .idle ? 1 : 0)
        .accessibilityLabel(theme?.label(state) ?? state.rawValue)
    }
}

extension View {
    /// A Liquid Glass card.
    func glassCard(_ tint: Color? = nil, radius: CGFloat = 24) -> some View {
        glassEffect(tint.map { .regular.tint($0.opacity(0.18)) } ?? .regular, in: .rect(cornerRadius: radius))
    }
}

/// "3 working · 1 needs you · $4.20 today" — the glance at the top of Active.
struct SummaryCard: View {
    @Environment(AppModel.self) private var model
    let snapshot: SessionsSnapshot

    var body: some View {
        let working = snapshot.working.count
        let needs = model.needsYou
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .bottom, spacing: 0) {
                metric("\(working)", label: "working", symbol: "bolt.fill", tint: model.accent, pulse: working > 0)
                Spacer(minLength: 8)
                metric("\(needs)", label: needs == 1 ? "needs you" : "need you", symbol: "hand.raised.fill", tint: needs > 0 ? .orange : .secondary, pulse: needs > 0)
                Spacer(minLength: 8)
                metric(money(snapshot.today?.cost) ?? "$0", label: "today", symbol: "dollarsign.circle.fill", tint: .green, pulse: false)
            }
            if let today = snapshot.today, today.turns > 0 {
                Label("\(count(today.turns, "turn")) · \(count(today.tools, "tool call")) today", systemImage: "chart.bar.fill")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .glassCard(model.accent, radius: 30)
    }

    private func count(_ value: Int, _ noun: String) -> String {
        "\(value.formatted(.number.locale(Locale(identifier: "en_US")))) \(noun)\(value == 1 ? "" : "s")"
    }

    private func metric(_ value: String, label: String, symbol: String, tint: Color, pulse: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: symbol).font(.subheadline.weight(.semibold)).foregroundStyle(tint).symbolEffect(.pulse, isActive: pulse)
            Text(value).font(.system(size: 34, weight: .bold, design: .rounded)).contentTransition(.numericText()).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct Badge: View {
    let text: String
    var tint: Color? = nil

    var body: some View {
        Text(text).font(.caption2.weight(.bold)).padding(.horizontal, 7).padding(.vertical, 2)
            .foregroundStyle(tint == nil ? Color.secondary : Color.black)
            .background(tint.map { AnyShapeStyle($0) } ?? AnyShapeStyle(.fill.tertiary), in: .capsule)
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
            .glassCard(radius: 18)
    }
}
