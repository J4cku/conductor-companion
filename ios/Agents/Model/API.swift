import Foundation

/// Wire types of the Mac's agent-status phone server (~/.claude/agent-status/phone/server.py).

enum AgentState: String, Codable, Sendable, CaseIterable {
    case waiting, failed, working, done, quiet, idle

    /// Order the Active tab groups by: what needs you first.
    static let attention: [AgentState] = [.waiting, .failed, .working, .done, .quiet]
}

struct Theme: Codable, Sendable, Equatable {
    let name: String
    let labels: [String: String]
    let icons: [String: String]

    func label(_ state: AgentState) -> String { labels[state.rawValue] ?? state.rawValue }
}

struct QuestionOption: Codable, Sendable, Hashable {
    let label: String
    let description: String?

    init(from decoder: Decoder) throws {
        if let text = try? decoder.singleValueContainer().decode(String.self) {
            label = text
            description = nil
            return
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decode(String.self, forKey: .label)
        description = try container.decodeIfPresent(String.self, forKey: .description)
    }
}

struct Question: Codable, Sendable, Hashable {
    let question: String
    let header: String?
    let options: [QuestionOption]?
}

/// A question an agent is holding for the phone while away mode is on.
struct Held: Codable, Sendable, Hashable {
    let kind: String
    let questions: [Question]?

    var question: Question? { questions?.first }
}

struct Session: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let state: AgentState
    let name: String
    let workspace: String?
    let doing: String?
    let unit: String
    let since: Double
    let context: Int?
    let quote: String
    let held: Held?
    let queued: Bool
    let place: String?

    /// The workspace folder, shown when it differs from the chat's own name.
    var placeLabel: String? { place.flatMap { $0 == name ? nil : $0 } }
}

struct SessionsSnapshot: Codable, Sendable, Equatable {
    let computer: String
    let away: Bool
    let theme: Theme
    let sessions: [Session]
}

struct Workspace: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let title: String
    let directory: String
    let branch: String?
    let status: String
    let pinned: Bool
    let state: AgentState
    let held: Bool
    let session: String?
    let chats: Int
    let updated: Double
}

struct Repository: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let name: String
    let workspaces: [Workspace]
}

struct WorkspacesSnapshot: Codable, Sendable, Equatable {
    let repos: [Repository]
}

struct Entry: Codable, Sendable, Hashable {
    enum Role: String, Codable, Sendable { case user, assistant, tool, note, phone }
    let role: Role
    let text: String
    let at: String?
    let status: String?
    let detail: String?
}

struct SessionMeta: Codable, Sendable, Hashable {
    let id: String
    let name: String
    let place: String?
    let state: AgentState
    let unit: String
    let since: Double
}

struct Transcript: Codable, Sendable, Equatable {
    let entries: [Entry]
    let held: Held?
    let session: SessionMeta?
    let reachable: Bool?
}

struct ReplyResult: Codable, Sendable {
    let status: String
    let detail: String
}

struct PairResult: Codable, Sendable {
    let token: String
    let computer: String
}

private struct Failure: Decodable { let error: String }

struct ServerError: LocalizedError, Sendable {
    let message: String
    let status: Int
    var errorDescription: String? { message }
}

/// Talks to one paired Mac.
struct APIClient: Sendable {
    let base: URL
    let token: String?

    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 15
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    func sessions() async throws -> SessionsSnapshot { try await get("/api/sessions") }
    func workspaces() async throws -> WorkspacesSnapshot { try await get("/api/workspaces") }
    func transcript(_ id: String, limit: Int = 120) async throws -> Transcript { try await get("/api/sessions/\(id)/messages?limit=\(limit)") }
    func reply(_ id: String, text: String) async throws -> ReplyResult { try await post("/api/sessions/\(id)/reply", ["text": text]) }

    func setAway(_ on: Bool) async throws -> Bool {
        struct Away: Decodable { let away: Bool }
        let result: Away = try await post("/api/away", ["on": on])
        return result.away
    }

    func pair(secret: String?, code: String?, device: String) async throws -> PairResult {
        var body = ["device": device]
        body["secret"] = secret
        body["code"] = code
        return try await post("/api/pair", body)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try await send(request(path, method: "GET"))
    }

    private func post<T: Decodable>(_ path: String, _ body: some Encodable) async throws -> T {
        var request = request(path, method: "POST")
        request.httpBody = try JSONEncoder().encode(body)
        return try await send(request)
    }

    private func request(_ path: String, method: String) -> URLRequest {
        var request = URLRequest(url: URL(string: path, relativeTo: base)!)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await Self.session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let message = (try? JSONDecoder().decode(Failure.self, from: data))?.error ?? HTTPURLResponse.localizedString(forStatusCode: status)
            throw ServerError(message: message, status: status)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
