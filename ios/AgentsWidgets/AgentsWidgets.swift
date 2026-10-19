import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

@main
struct AgentsWidgets: WidgetBundle {
    var body: some Widget {
        StatusWidget()
        AgentsLiveActivity()
        AwayControl()
    }
}

private let accent = Color(hex: SharedStore.defaults.string(forKey: "accent"))

// MARK: Live Activity

struct AgentsLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: AgentsActivityAttributes.self) { context in
            LockScreenActivity(state: context.state, computer: context.attributes.computer)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(accent)
        } dynamicIsland: { context in
            let state = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Counter(value: state.working, label: "working", symbol: "bolt.fill", tint: accent)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Counter(value: state.needsYou, label: "need you", symbol: "hand.raised.fill", tint: state.needsYou > 0 ? .orange : .secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(state.headline.isEmpty ? "All quiet" : state.headline).font(.headline).lineLimit(1)
                        Text([state.unit, state.detail].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                Label("\(state.working)", systemImage: "bolt.fill").labelStyle(.titleAndIcon).foregroundStyle(accent).font(.caption.weight(.bold))
            } compactTrailing: {
                if state.needsYou > 0 {
                    Image(systemName: "hand.raised.fill").foregroundStyle(.orange)
                } else {
                    Image(systemName: "ellipsis").symbolEffect(.variableColor.iterative, isActive: state.working > 0).foregroundStyle(accent)
                }
            } minimal: {
                Text("\(state.working)").font(.caption.weight(.bold)).foregroundStyle(state.needsYou > 0 ? .orange : accent)
            }
            .keylineTint(accent)
        }
    }
}

private struct Counter: View {
    let value: Int
    let label: String
    let symbol: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Label("\(value)", systemImage: symbol).font(.title2.weight(.bold)).foregroundStyle(tint).contentTransition(.numericText())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
    }
}

private struct LockScreenActivity: View {
    let state: AgentsActivityAttributes.ContentState
    let computer: String

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().fill(accent.opacity(0.2))
                Image(systemName: state.needsYou > 0 ? "hand.raised.fill" : "bolt.fill")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(state.needsYou > 0 ? .orange : accent)
                    .symbolEffect(.pulse, isActive: state.working > 0)
            }
            .frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 3) {
                Text(state.headline.isEmpty ? "All quiet on \(computer)" : state.headline).font(.headline).lineLimit(1)
                Text([state.unit, state.detail].filter { !$0.isEmpty }.joined(separator: " · ")).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing: 10) {
                    Label("\(state.working) working", systemImage: "bolt.fill").foregroundStyle(accent)
                    if state.needsYou > 0 { Label("\(state.needsYou) need you", systemImage: "hand.raised.fill").foregroundStyle(.orange) }
                }
                .font(.caption.weight(.semibold))
            }
            Spacer(minLength: 0)
        }
        .padding(16)
    }
}

// MARK: Widgets

struct StatusEntry: TimelineEntry {
    let date: Date
    let snapshot: SessionsSnapshot?
}

struct StatusProvider: TimelineProvider {
    func placeholder(in context: Context) -> StatusEntry { StatusEntry(date: .now, snapshot: SharedStore.lastSnapshot) }

    func getSnapshot(in context: Context, completion: @escaping (StatusEntry) -> Void) {
        completion(StatusEntry(date: .now, snapshot: SharedStore.lastSnapshot))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<StatusEntry>) -> Void) {
        nonisolated(unsafe) let completion = completion  // WidgetKit calls this once, from any thread
        Task {
            var snapshot = SharedStore.lastSnapshot
            if let client = SharedStore.currentMac?.client, let fresh = try? await client.sessions() {
                snapshot = fresh
                SharedStore.lastSnapshot = fresh
            }
            completion(Timeline(entries: [StatusEntry(date: .now, snapshot: snapshot)], policy: .after(.now.addingTimeInterval(15 * 60))))
        }
    }
}

struct StatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "status", provider: StatusProvider()) { entry in
            StatusWidgetView(entry: entry)
        }
        .configurationDisplayName("Agents")
        .description("What your agents are doing, and which need you.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

struct StatusWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: StatusEntry

    private var snapshot: SessionsSnapshot? { entry.snapshot }
    private var working: Int { snapshot?.working.count ?? 0 }
    private var needs: Int { snapshot?.needingYou.count ?? 0 }
    private var tint: Color { snapshot?.theme.accentColor ?? accent }

    var body: some View {
        content
            .containerBackground(for: .widget) {
                LinearGradient(colors: [tint.opacity(0.35), Color.black.opacity(0.9)], startPoint: .topLeading, endPoint: .bottomTrailing)
            }
            .widgetURL(URL(string: snapshot?.needingYou.first.map { "agents://session/\($0.id)" } ?? "agents://home"))
    }

    @ViewBuilder private var content: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 0) {
                    Image(systemName: needs > 0 ? "hand.raised.fill" : "bolt.fill").font(.caption)
                    Text("\(needs > 0 ? needs : working)").font(.title3.weight(.bold))
                }
            }
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 1) {
                Label("\(working) working", systemImage: "bolt.fill").font(.headline)
                Text(needs > 0 ? "\(needs) need you" : (snapshot?.working.first?.name ?? "All quiet")).font(.caption).lineLimit(1)
            }
        case .accessoryInline:
            Label(needs > 0 ? "\(needs) agents need you" : "\(working) agents working", systemImage: "bolt.fill")
        case .systemMedium:
            HStack(alignment: .top, spacing: 14) {
                summary
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array((snapshot?.needingYou ?? []) + (snapshot?.working ?? [])).prefix(3), id: \.id) { session in
                        HStack(spacing: 8) {
                            if let image = snapshot?.theme.image(session.asking ? .waiting : session.state) {
                                Image(uiImage: image).resizable().interpolation(.none).frame(width: 18, height: 18)
                            }
                            VStack(alignment: .leading, spacing: 0) {
                                Text(session.name).font(.caption.weight(.semibold)).lineLimit(1)
                                Text(session.activity).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                    }
                    if snapshot?.sessions.isEmpty ?? true { Text("All quiet").font(.caption).foregroundStyle(.secondary) }
                }
            }
        default:
            summary
        }
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let image = snapshot?.theme.image(needs > 0 ? .waiting : working > 0 ? .working : .idle) {
                Image(uiImage: image).resizable().interpolation(.none).frame(width: 30, height: 30)
            }
            Spacer(minLength: 0)
            Text("\(working)").font(.system(size: 40, weight: .bold, design: .rounded)).foregroundStyle(tint)
            Text("working").font(.caption).foregroundStyle(.secondary)
            if needs > 0 { Text("\(needs) need you").font(.caption.weight(.semibold)).foregroundStyle(.orange) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: Control Center

struct AwayValueProvider: ControlValueProvider {
    var previewValue: Bool { false }

    func currentValue() async throws -> Bool {
        guard let client = SharedStore.currentMac?.client else { return false }
        return try await client.sessions().away
    }
}

struct AwayControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "away", provider: AwayValueProvider()) { away in
            ControlWidgetToggle("Away mode", isOn: away, action: SetAwayIntent()) { on in
                Label(on ? "Away" : "At desk", systemImage: on ? "figure.walk.departure" : "laptopcomputer")
            }
        }
        .displayName("Agents away mode")
        .description("Agents' questions wait for your phone.")
    }
}
