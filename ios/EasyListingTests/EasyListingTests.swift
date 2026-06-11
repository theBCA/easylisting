import Testing
import Foundation
@testable import EasyListing

// MARK: - Keychain

struct KeychainTests {
    @Test func saveLoadDelete() throws {
        let key = "test_token_\(UUID().uuidString)"
        KeychainHelper.save("tok123", key: key)
        #expect(KeychainHelper.load(key: key) == "tok123")
        KeychainHelper.save("tok456", key: key)  // overwrite
        #expect(KeychainHelper.load(key: key) == "tok456")
        KeychainHelper.delete(key: key)
        #expect(KeychainHelper.load(key: key) == nil)
    }
}

// MARK: - GenerateResponse decoding (mirrors backend /api/generate payloads)

struct GenerateResponseDecodingTests {
    private func decode(_ json: String) throws -> GenerateResponse {
        try JSONDecoder().decode(GenerateResponse.self, from: Data(json.utf8))
    }

    @Test func etsyPayload() throws {
        let r = try decode("""
        {"title":"Mug","description":"Nice mug","tags":["mug","gift"],
         "materials":["ceramic"],"suggested_price_eur":24.5,
         "personalization_instructions":"Add name",
         "taxonomy_id":123,"taxonomy_path":"Home > Mugs",
         "shipping_profiles":[{"shipping_profile_id":9,"title":"Standard"}],
         "image_previews":["data:image/jpeg;base64,xx"]}
        """)
        #expect(r.title == "Mug")
        #expect(r.price == 24.5)
        #expect(r.taxonomyId == 123)
        #expect(r.shippingProfiles.first?.id == 9)
        #expect(r.imagePreviews.count == 1)
    }

    @Test func trendyolPayloadWithTryPrice() throws {
        let r = try decode("""
        {"title":"Kupa","description":"<p>Guzel</p>","tags":["kupa"],
         "suggested_price_try":350,"taxonomy_id":null,"taxonomy_path":null,
         "shipping_profiles":[],"image_previews":[]}
        """)
        #expect(r.price == 350)
        #expect(r.taxonomyId == nil)
    }

    @Test func priceAsString() throws {
        let r = try decode("""
        {"title":"X","description":"Y","tags":[],"suggested_price_eur":"19,99",
         "shipping_profiles":[],"image_previews":[]}
        """)
        #expect(r.price == 19.99)
    }

    @Test func shopifyDescriptionHTMLFallback() throws {
        let r = try decode("""
        {"title":"X","description_html":"<p>HTML body</p>","tags":[],
         "suggested_price_eur":10,"shipping_profiles":[],"image_previews":[]}
        """)
        #expect(r.description == "<p>HTML body</p>")
    }

    @Test func missingOptionalFieldsDoNotThrow() throws {
        let r = try decode(#"{"title":"X","description":"Y","tags":["a"]}"#)
        #expect(r.price == 0)
        #expect(r.materials.isEmpty)
        #expect(r.shippingProfiles.isEmpty)
    }
}

// MARK: - PublishRequest encoding (must match backend validate_listing_input)

struct PublishRequestEncodingTests {
    @Test func encodesSnakeCaseKeys() throws {
        let req = PublishRequest(
            title: "T", description: "D", tags: ["a"], materials: ["m"],
            price: 9.99, taxonomyId: 5, shippingProfileId: 7,
            personalizationInstructions: "pi",
            imagePreviews: ["data:image/jpeg;base64,xx"]
        )
        let dict = try #require(
            try JSONSerialization.jsonObject(with: JSONEncoder().encode(req)) as? [String: Any]
        )
        #expect(dict["taxonomy_id"] as? Int == 5)
        #expect(dict["shipping_profile_id"] as? Int == 7)
        #expect(dict["personalization_instructions"] as? String == "pi")
        #expect((dict["image_previews"] as? [String])?.count == 1)
    }
}

// MARK: - Response models

struct ResponseModelTests {
    @Test func publishResponse() throws {
        let r = try JSONDecoder().decode(PublishResponse.self,
            from: Data(#"{"success":true,"listing_id":42}"#.utf8))
        #expect(r.success)
        #expect(r.listingId == 42)
    }

    @Test func statusResponse() throws {
        let r = try JSONDecoder().decode(StatusResponse.self,
            from: Data(#"{"allowed":true,"remaining":3,"is_guest":true,"trendyol_connected":false}"#.utf8))
        #expect(r.remaining == 3)
        #expect(r.isGuest)
    }

    @Test func magicLinkInstantLogin() throws {
        let r = try JSONDecoder().decode(MagicLinkResponse.self,
            from: Data(#"{"ok":true,"already_verified":true,"mobile_token":"abc"}"#.utf8))
        #expect(r.mobileToken == "abc")
    }

    @Test func magicLinkEmailSent() throws {
        let r = try JSONDecoder().decode(MagicLinkResponse.self,
            from: Data(#"{"ok":true}"#.utf8))
        #expect(r.mobileToken == nil)
    }

    @Test func listingPriceDisplay() throws {
        let p = try JSONDecoder().decode(ListingPrice.self,
            from: Data(#"{"amount":2450,"divisor":100,"currency_code":"USD"}"#.utf8))
        #expect(p.display == "USD 24.50")
    }

    @Test func listingPriceZeroDivisorSafe() throws {
        let p = try JSONDecoder().decode(ListingPrice.self,
            from: Data(#"{"amount":100,"divisor":0,"currency_code":"USD"}"#.utf8))
        #expect(p.display == "USD 100.00")  // guarded against division by zero
    }

    @Test func improveResponsePartialFields() throws {
        let r = try JSONDecoder().decode(ImproveResponse.self,
            from: Data(#"{"title":"New"}"#.utf8))
        #expect(r.title == "New")
        #expect(r.description == nil)
    }

    @Test func improveActionRawValues() {
        #expect(ImproveAction.titleSEO.rawValue == "title_seo")
        #expect(ImproveAction.descriptionWarm.rawValue == "description_warm")
        #expect(ImproveAction.tags.rawValue == "tags")
        #expect(ImproveAction.shorten.rawValue == "shorten_etsy")
    }

    @Test func photoVariantsResponse() throws {
        let r = try JSONDecoder().decode(PhotoVariantsResponse.self,
            from: Data(#"{"variants":[{"label":"Lifestyle","image":"data:image/jpeg;base64,x"}],"remaining":27}"#.utf8))
        #expect(r.variants.first?.label == "Lifestyle")
        #expect(r.remaining == 27)
    }
}
