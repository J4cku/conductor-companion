import AppIntents
import Foundation

/// A chat on the paired Mac, for Siri, Shortcuts and widgets.
struct AgentEntity: AppEntity, Identifiable {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Agent"
    static let defaultQuery = AgentQuery()

    let id: String
    let name: String
    let place: String?

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: place.map { "\($0)" })
    }
}

struct AgentQuery: EntityStringQuery {
    func entities(for identifiers: [AgentEntity.ID]) async throws -> [AgentEntity] {
        try await all().filter { identifiers.contains($0.id) }
    }

    func entities(matching string: String) async throws -> [AgentEntity] {
        try await all().filter { $0.name.localizedCaseInsensitiveContains(string) || ($0.place?.localizedCaseInsensitiveContains(string) ?? false) }
    }

    func suggestedEntities() async throws -> [AgentEntity] { try await all() }

    /// Active chats first, then every workspace's current chat.
    private func all() async throws -> [AgentEntity] {
        guard let client = SharedStore.currentMac?.client else { return [] }
        async let sessions = client.sessions()
        async let workspaces = client.workspaces()
        var seen = Set<String>()
        var result: [AgentEntity] = []
        for session in try await sessions.sessions where seen.insert(session.id).inserted {
            result.append(AgentEntity(id: session.id, name: session.name, place: session.placeLabel))
        }
        for repo in (try? await workspaces.repos) ?? [] {
            for workspace in repo.workspaces {
                guard let id = workspace.session, seen.insert(id).inserted else { continue }
                result.append(AgentEntity(id: id, name: workspace.title, place: workspace.title == workspace.directory ? repo.name : workspace.directory))
            }
        }
        return result
    }
}

struct MessageAgentIntent: AppIntent {
    static let title: LocalizedStringResource = "Message an Agent"
    static let description = IntentDescription("Send a message to one of your coding agents.")

    @Parameter(title: "Agent") var agent: AgentEntity
    @Parameter(title: "Message", inputOptions: String.IntentInputOptions(multiline: true)) var message: String

    static var parameterSummary: some ParameterSummary { Summary("Tell \(\.$agent) \(\.$message)") }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let client = SharedStore.currentMac?.client else { throw ServerError(message: "Pair the app with your computer first.", status: 0) }
        let result = try await client.reply(agent.id, text: message)
        return .result(dialog: "\(result.detail)")
    }
}

struct OpenAgentIntent: AppIntent {
    static let title: LocalizedStringResource = "Open an Agent"
    static let openAppWhenRun = true

    @Parameter(title: "Agent") var agent: AgentEntity

    func perform() async throws -> some IntentResult {
        SharedStore.defaults.set(agent.id, forKey: "pendingSession")
        return .result()
    }
}

/// Away mode: agents' questions wait for the phone. Also the Control Center toggle.
struct SetAwayIntent: SetValueIntent {
    static let title: LocalizedStringResource = "Set Away Mode"

    @Parameter(title: "Away") var value: Bool

    func perform() async throws -> some IntentResult {
        guard let client = SharedStore.currentMac?.client else { return .result() }
        _ = try await client.setAway(value)
        return .result()
    }
}
