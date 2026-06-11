import Foundation

// MARK: - Auth

struct StatusResponse: Codable {
    let allowed: Bool
    let remaining: Int
    let isGuest: Bool
    let isEmail: Bool
    let trendyolConnected: Bool

    enum CodingKeys: String, CodingKey {
        case allowed, remaining
        case isGuest = "is_guest"
        case isEmail = "is_email"
        case trendyolConnected = "trendyol_connected"
    }
}

struct CSRFResponse: Codable {
    let csrfToken: String
    enum CodingKeys: String, CodingKey { case csrfToken = "csrf_token" }
}

struct MobileGuestResponse: Codable {
    let ok: Bool
    let mobileToken: String
    enum CodingKeys: String, CodingKey { case ok; case mobileToken = "mobile_token" }
}

struct MagicLinkResponse: Codable {
    let ok: Bool
    let alreadyVerified: Bool?
    let mobileToken: String?
    enum CodingKeys: String, CodingKey {
        case ok
        case alreadyVerified = "already_verified"
        case mobileToken     = "mobile_token"
    }
}

struct MagicVerifyResponse: Codable {
    let ok: Bool
    let mobileToken: String
    let shopId: String
    enum CodingKeys: String, CodingKey {
        case ok
        case mobileToken = "mobile_token"
        case shopId      = "shop_id"
    }
}

// MARK: - Generate
// Backend returns the raw AI JSON enriched with taxonomy/shipping/image fields.
// Price arrives as suggested_price_eur / _usd / _try depending on platform,
// possibly as a number or a string — decode defensively.

struct GenerateResponse: Decodable {
    let title: String
    let description: String
    let tags: [String]
    let materials: [String]
    let price: Double
    let personalizationInstructions: String
    let taxonomyId: Int?
    let taxonomyPath: String?
    let shippingProfiles: [ShippingProfile]
    let imagePreviews: [String]

    private struct Dyn: CodingKey {
        var stringValue: String; var intValue: Int? = nil
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Dyn.self)
        func str(_ k: String) -> String { (try? c.decode(String.self, forKey: Dyn(stringValue: k)!)) ?? "" }
        func strList(_ k: String) -> [String] { (try? c.decode([String].self, forKey: Dyn(stringValue: k)!)) ?? [] }
        func num(_ k: String) -> Double? {
            let key = Dyn(stringValue: k)!
            if let d = try? c.decode(Double.self, forKey: key) { return d }
            if let s = try? c.decode(String.self, forKey: key) { return Double(s.replacingOccurrences(of: ",", with: ".")) }
            return nil
        }

        title       = str("title")
        let desc    = str("description")
        description = desc.isEmpty ? str("description_html") : desc
        tags        = strList("tags")
        materials   = strList("materials")
        price       = num("price") ?? num("suggested_price_eur") ?? num("suggested_price_usd")
                      ?? num("suggested_price_try") ?? 0
        personalizationInstructions = str("personalization_instructions")
        taxonomyId   = try? c.decode(Int.self, forKey: Dyn(stringValue: "taxonomy_id")!)
        taxonomyPath = try? c.decode(String.self, forKey: Dyn(stringValue: "taxonomy_path")!)
        shippingProfiles = (try? c.decode([ShippingProfile].self, forKey: Dyn(stringValue: "shipping_profiles")!)) ?? []
        imagePreviews    = strList("image_previews")
    }
}

struct ShippingProfile: Codable, Identifiable {
    let shippingProfileId: Int
    let title: String
    var id: Int { shippingProfileId }
    enum CodingKeys: String, CodingKey {
        case shippingProfileId = "shipping_profile_id"
        case title
    }
}

// MARK: - Publish
// Backend requires taxonomy_id > 0 and uploads images from image_previews
// (base64 data URLs). quantity is fixed server-side.

struct PublishRequest: Codable {
    var title: String
    var description: String
    var tags: [String]
    var materials: [String]
    var price: Double
    var taxonomyId: Int
    var shippingProfileId: Int?
    var personalizationInstructions: String
    var imagePreviews: [String]

    enum CodingKeys: String, CodingKey {
        case title, description, tags, materials, price
        case taxonomyId        = "taxonomy_id"
        case shippingProfileId = "shipping_profile_id"
        case personalizationInstructions = "personalization_instructions"
        case imagePreviews     = "image_previews"
    }
}

struct PublishResponse: Codable {
    let success: Bool
    let listingId: Int
    enum CodingKeys: String, CodingKey {
        case success
        case listingId = "listing_id"
    }
}

// MARK: - Listings

struct ListingsResponse: Codable {
    let results: [EtsyListing]
}

struct EtsyListing: Codable, Identifiable {
    let listingId: Int
    let title: String
    let description: String?
    let price: ListingPrice?
    let state: String?
    let images: [ListingImage]?
    let tags: [String]?
    var id: Int { listingId }
    enum CodingKeys: String, CodingKey {
        case listingId = "listing_id"
        case title, description, price, state, images, tags
    }
}

struct ListingPrice: Codable {
    let amount: Int
    let divisor: Int
    let currencyCode: String
    var display: String { "\(currencyCode) \(String(format: "%.2f", Double(amount) / Double(max(divisor, 1))))" }
    enum CodingKeys: String, CodingKey {
        case amount, divisor
        case currencyCode = "currency_code"
    }
}

struct ListingImage: Codable {
    let url570xN: String?
    enum CodingKeys: String, CodingKey { case url570xN = "url_570xN" }
}

// MARK: - Improve
// Contract: POST {action, lang, title, description, tags, materials, price, category}
// action ∈ title_seo | description_warm | tags | shorten_etsy
// Response: {title, description, tags}

enum ImproveAction: String, CaseIterable, Identifiable {
    case titleSEO        = "title_seo"
    case descriptionWarm = "description_warm"
    case tags            = "tags"
    case shorten         = "shorten_etsy"
    var id: String { rawValue }
}

struct ImproveResponse: Decodable {
    let title: String?
    let description: String?
    let tags: [String]?
}

// MARK: - Translate
// Contract: POST {lang ∈ en|de|tr, title, description, tags}

struct TranslateResponse: Codable {
    let title: String
    let description: String
    let tags: [String]
}

// MARK: - Photo variants (Pro)
// Contract: POST {image: dataURL, title, materials, colors, category}
// Response: {variants: [{label, image}], remaining}

struct PhotoVariantsResponse: Codable {
    let variants: [PhotoVariant]
    let remaining: Int
}

struct PhotoVariant: Codable, Identifiable {
    let label: String
    let image: String
    var id: String { label }
}

// MARK: - Trendyol

struct TrendyolConnectRequest: Codable {
    let supplierId: String
    let apiKey: String
    let apiSecret: String
    enum CodingKeys: String, CodingKey {
        case supplierId = "supplier_id"
        case apiKey     = "api_key"
        case apiSecret  = "api_secret"
    }
}

// MARK: - API Error

struct APIError: Codable {
    let error: String
}
