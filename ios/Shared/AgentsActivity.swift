import ActivityKit
import Foundation

/// The Live Activity: how many agents are working, how many need you, and what the busiest one is doing.
/// The content state's keys match what `mac/server/server.py` pushes.
struct AgentsActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var working: Int
        var needsYou: Int
        var headline: String
        var detail: String
        var unit: String
        var updatedAt: Double
    }

    var computer: String
}

extension AgentsActivityAttributes.ContentState {
    init(_ snapshot: SessionsSnapshot) {
        let lead = snapshot.working.max { $0.since < $1.since }
        self.init(working: snapshot.working.count, needsYou: snapshot.needingYou.count, headline: lead?.name ?? "",
                  detail: lead?.activity ?? "", unit: lead?.unit ?? "", updatedAt: Date.now.timeIntervalSince1970)
    }
}
