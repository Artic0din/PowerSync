//
//  HomeAssistantClient.swift
//  PowerSync
//
//  Native URLSession client for Home Assistant REST API.
//  No third-party networking libraries.
//

import Foundation

enum HomeAssistantClientError: LocalizedError, Sendable {
    case notConfigured
    case invalidURL
    case unauthorized
    case badStatus(Int)
    case decodingFailed
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            "The Home Assistant client is not configured. Please enter a URL and access token."
        case .invalidURL:
            "The Home Assistant URL is invalid."
        case .unauthorized:
            "Unauthorized. Check your long-lived access token."
        case .badStatus(let code):
            "Home Assistant returned HTTP \(code)."
        case .decodingFailed:
            "Could not decode the Home Assistant response."
        case .transport(let message):
            message
        }
    }
}

struct HomeAssistantState: Decodable, Sendable {
    var entityId: String
    var state: String
    var attributes: [String: AnyCodable]

    enum CodingKeys: String, CodingKey {
        case entityId = "entity_id"
        case state
        case attributes
    }
}

/// Minimal type-erased Codable box for HA attributes.
struct AnyCodable: Decodable, Sendable {
    let value: String

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) {
            value = string
        } else if let bool = try? container.decode(Bool.self) {
            value = bool ? "true" : "false"
        } else if let int = try? container.decode(Int.self) {
            value = String(int)
        } else if let double = try? container.decode(Double.self) {
            value = String(double)
        } else {
            value = ""
        }
    }
}

actor HomeAssistantClient {
    private let session: URLSession
    private var baseURL: URL?
    private var token: String?

    init(session: URLSession = .shared) {
        self.session = session
    }

    func configure(baseURLString: String, token: String) throws {
        guard let url = URL(string: baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "http" || url.scheme == "https"
        else {
            throw HomeAssistantClientError.invalidURL
        }
        self.baseURL = url
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func clear() {
        baseURL = nil
        token = nil
    }

    func fetchStates() async throws -> [HomeAssistantState] {
        try await get(path: "/api/states", as: [HomeAssistantState].self)
    }

    func callService(domain: String, service: String, data: [String: String] = [:]) async throws {
        var request = try makeRequest(path: "/api/services/\(domain)/\(service)")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: data)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw HomeAssistantClientError.transport("Invalid response.")
        }
        if http.statusCode == 401 || http.statusCode == 403 {
            throw HomeAssistantClientError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            throw HomeAssistantClientError.badStatus(http.statusCode)
        }
    }

    func ping() async throws {
        struct ConfigResponse: Decodable {
            var locationName: String?
            enum CodingKeys: String, CodingKey { case locationName = "location_name" }
        }
        _ = try await get(path: "/api/config", as: ConfigResponse.self)
    }

    private func get<T: Decodable>(path: String, as type: T.Type) async throws -> T {
        var request = try makeRequest(path: path)
        request.httpMethod = "GET"
        return try await send(request, as: type)
    }

    private func makeRequest(path: String) throws -> URLRequest {
        guard let baseURL, let token, !token.isEmpty else {
            throw HomeAssistantClientError.notConfigured
        }
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw HomeAssistantClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 20
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw HomeAssistantClientError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw HomeAssistantClientError.transport("Invalid response.")
        }
        if http.statusCode == 401 || http.statusCode == 403 {
            throw HomeAssistantClientError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            throw HomeAssistantClientError.badStatus(http.statusCode)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw HomeAssistantClientError.decodingFailed
        }
    }
}
