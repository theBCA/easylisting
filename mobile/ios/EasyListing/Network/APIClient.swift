import Foundation
import UIKit

// MARK: - Config

enum Config {
    static let baseURL = "https://easylisting.app"
}

// MARK: - Network errors

enum NetworkError: LocalizedError, Equatable {
    case invalidURL
    case noData
    case decodingFailed(String)
    case serverError(String)
    case unauthorized
    case limitReached
    case emailVerificationRequired
    case premiumRequired
    case proRequired

    var errorDescription: String? {
        switch self {
        case .invalidURL:            return String(localized: "Invalid URL.")
        case .noData:                return String(localized: "No response from server.")
        case .decodingFailed:        return String(localized: "Unexpected server response. Please update the app and try again.")
        case .serverError(let msg):  return msg
        case .unauthorized:          return String(localized: "Session expired. Please sign in again.")
        case .limitReached:          return String(localized: "You've used all your free generations.")
        case .emailVerificationRequired: return String(localized: "Please verify your email to continue.")
        case .premiumRequired:       return String(localized: "This feature requires a Premium plan.")
        case .proRequired:           return String(localized: "This feature requires the Pro plan.")
        }
    }
}

// MARK: - APIClient

@MainActor
final class APIClient: ObservableObject {
    static let shared = APIClient()

    private let session: URLSession
    private var csrfToken: String?

    private init() {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.httpShouldSetCookies = true
        config.httpCookieAcceptPolicy = .always
        config.timeoutIntervalForRequest = 120  // AI generation can be slow
        session = URLSession(configuration: config)
    }

    // MARK: - Mobile token

    var mobileToken: String? {
        get { KeychainHelper.load(key: "mobile_token") }
        set {
            if let v = newValue { KeychainHelper.save(v, key: "mobile_token") }
            else                { KeychainHelper.delete(key: "mobile_token") }
        }
    }

    // MARK: - Request plumbing

    private func url(_ path: String) throws -> URL {
        guard let url = URL(string: Config.baseURL + path) else { throw NetworkError.invalidURL }
        return url
    }

    private func commonHeaders() -> [String: String] {
        var h: [String: String] = ["X-Mobile-Request": "true"]
        if let tok = mobileToken { h["X-Mobile-Token"] = tok }
        if let csrf = csrfToken  { h["X-CSRFToken"] = csrf }
        return h
    }

    private func fetchCSRFIfNeeded() async throws {
        guard csrfToken == nil else { return }
        let resp: CSRFResponse = try await get("/api/csrf-token")
        csrfToken = resp.csrfToken
    }

    func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem] = []) async throws -> T {
        guard var components = URLComponents(string: Config.baseURL + path) else { throw NetworkError.invalidURL }
        if !queryItems.isEmpty { components.queryItems = (components.queryItems ?? []) + queryItems }
        guard let url = components.url else { throw NetworkError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
        return try await perform(req)
    }

    func post<T: Decodable>(_ path: String, body: some Encodable) async throws -> T {
        try await fetchCSRFIfNeeded()
        var req = URLRequest(url: try url(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
        req.httpBody = try JSONEncoder().encode(body)
        return try await perform(req)
    }

    func postMultipart<T: Decodable>(
        _ path: String,
        fields: [String: String],
        images: [UIImage]
    ) async throws -> T {
        try await fetchCSRFIfNeeded()
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: try url(path))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
        req.httpBody = buildMultipart(boundary: boundary, fields: fields, images: images)
        return try await perform(req)
    }

    private func buildMultipart(boundary: String, fields: [String: String], images: [UIImage]) -> Data {
        var data = Data()
        let crlf = "\r\n"

        for (key, value) in fields {
            data.append("--\(boundary)\(crlf)")
            data.append("Content-Disposition: form-data; name=\"\(key)\"\(crlf)\(crlf)")
            data.append(value + crlf)
        }

        for (i, img) in images.enumerated() {
            guard let jpeg = compressedJPEG(img) else { continue }
            data.append("--\(boundary)\(crlf)")
            data.append("Content-Disposition: form-data; name=\"images\"; filename=\"image\(i).jpg\"\(crlf)")
            data.append("Content-Type: image/jpeg\(crlf)\(crlf)")
            data.append(jpeg)
            data.append(crlf)
        }
        data.append("--\(boundary)--\(crlf)")
        return data
    }

    /// Downscale + compress so 5 photos stay well under the 30 MB request cap.
    private func compressedJPEG(_ image: UIImage, maxDimension: CGFloat = 2048) -> Data? {
        var img = image
        let largest = max(image.size.width, image.size.height)
        if largest > maxDimension {
            let scale = maxDimension / largest
            let newSize = CGSize(width: image.size.width * scale, height: image.size.height * scale)
            let renderer = UIGraphicsImageRenderer(size: newSize)
            img = renderer.image { _ in image.draw(in: CGRect(origin: .zero, size: newSize)) }
        }
        return img.jpegData(compressionQuality: 0.82)
    }

    private func perform<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw NetworkError.noData }

        if !(200..<300).contains(http.statusCode) {
            let code = (try? JSONDecoder().decode(APIError.self, from: data))?.error
            switch (http.statusCode, code) {
            case (401, _):                                  throw NetworkError.unauthorized
            case (403, "free_limit_reached"),
                 (403, "limit_reached"):                    throw NetworkError.limitReached
            case (403, "email_verification_required"):      throw NetworkError.emailVerificationRequired
            case (403, "premium_required"):                 throw NetworkError.premiumRequired
            case (403, "pro_required"),
                 (403, "photo_limit_reached"):              throw NetworkError.proRequired
            case (_, let msg?):                             throw NetworkError.serverError(msg)
            default:
                throw NetworkError.serverError(String(localized: "Something went wrong. Please try again."))
            }
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw NetworkError.decodingFailed(String(describing: error))
        }
    }

    // MARK: - Auth

    func fetchStatus() async throws -> StatusResponse {
        try await get("/api/status")
    }

    func guestLogin() async throws -> String {
        var req = URLRequest(url: try url("/auth/mobile/guest"))
        req.httpMethod = "POST"
        commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
        let resp: MobileGuestResponse = try await perform(req)
        return resp.mobileToken
    }

    func requestMagicLink(email: String, marketingConsent: Bool) async throws -> MagicLinkResponse {
        struct Body: Encodable {
            let email: String
            let marketing_consent: Bool
        }
        return try await post("/api/magic-link", body: Body(email: email, marketing_consent: marketingConsent))
    }

    func verifyMagicToken(_ token: String) async throws -> MagicVerifyResponse {
        guard let encoded = token.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
            throw NetworkError.invalidURL
        }
        var req = URLRequest(url: try url("/auth/magic?token=\(encoded)"))
        req.httpMethod = "GET"
        commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
        return try await perform(req)
    }

    func logout() async {
        struct Empty: Decodable {}
        if mobileToken != nil, let logoutURL = try? url("/auth/mobile/logout") {
            var req = URLRequest(url: logoutURL)
            req.httpMethod = "POST"
            commonHeaders().forEach { req.setValue($1, forHTTPHeaderField: $0) }
            _ = try? await perform(req) as Empty
        }
        mobileToken = nil
        csrfToken = nil
    }

    // MARK: - Generate

    func generate(images: [UIImage], hint: String, lang: String, platform: String) async throws -> GenerateResponse {
        try await postMultipart("/api/generate", fields: [
            "hint":     hint,
            "lang":     lang,
            "platform": platform,
        ], images: images)
    }

    // MARK: - Publish

    func publish(_ req: PublishRequest) async throws -> PublishResponse {
        try await post("/api/publish", body: req)
    }

    // MARK: - Listings

    func listings(state: String) async throws -> [EtsyListing] {
        let resp: ListingsResponse = try await get("/api/listings", queryItems: [URLQueryItem(name: "state", value: state)])
        return resp.results
    }

    // MARK: - Improve

    func improveListing(
        action: ImproveAction, lang: String,
        title: String, description: String, tags: [String],
        materials: [String] = [], price: String = "", category: String = ""
    ) async throws -> ImproveResponse {
        struct Body: Encodable {
            let action, lang, title, description, price, category: String
            let tags, materials: [String]
        }
        return try await post("/api/improve-listing", body: Body(
            action: action.rawValue, lang: lang, title: title, description: description,
            price: price, category: category, tags: tags, materials: materials
        ))
    }

    // MARK: - Translate (backend supports en / de / tr)

    func translate(title: String, description: String, tags: [String], lang: String) async throws -> TranslateResponse {
        struct Body: Encodable {
            let lang, title, description: String
            let tags: [String]
        }
        return try await post("/api/translate", body: Body(lang: lang, title: title, description: description, tags: tags))
    }

    // MARK: - Photo variants (Pro)

    func generatePhotoVariants(imageDataURL: String, title: String, materials: [String], category: String) async throws -> PhotoVariantsResponse {
        struct Body: Encodable {
            let image, title, category: String
            let materials: [String]
        }
        return try await post("/api/generate-photos", body: Body(
            image: imageDataURL, title: title, category: category, materials: materials
        ))
    }

    // MARK: - Stripe

    func stripeCheckoutURL(plan: String) async throws -> URL {
        struct Body: Encodable { let plan: String }
        struct Resp: Decodable { let url: String }
        let resp: Resp = try await post("/api/stripe/checkout", body: Body(plan: plan))
        guard let url = URL(string: resp.url) else { throw NetworkError.decodingFailed("Invalid checkout URL") }
        return url
    }

    // MARK: - Trendyol

    func trendyolConnect(supplierId: String, apiKey: String, apiSecret: String) async throws {
        struct OkResp: Decodable { let ok: Bool }
        let body = TrendyolConnectRequest(supplierId: supplierId, apiKey: apiKey, apiSecret: apiSecret)
        let _: OkResp = try await post("/api/trendyol/connect", body: body)
    }

    func trendyolDisconnect() async throws {
        struct OkResp: Decodable { let ok: Bool }
        struct Empty: Encodable {}
        let _: OkResp = try await post("/api/trendyol/disconnect", body: Empty())
    }
}

// MARK: - Helpers

private extension Data {
    mutating func append(_ string: String) {
        if let d = string.data(using: .utf8) { append(d) }
    }
}
