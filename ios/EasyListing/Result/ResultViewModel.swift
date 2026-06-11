import SwiftUI

@Observable
@MainActor
final class ResultViewModel {
    var title: String
    var description: String
    var tags: [String]
    var materials: [String]
    var price: Double
    var personalizationInstructions: String
    var shippingProfileId: Int?

    var isPublishing  = false
    var isImproving   = false
    var error: String?
    var successMessage: String?
    var publishedListingId: Int?
    var showTranslate = false
    var needsUpgrade  = false

    let result: GenerateResponse

    init(result: GenerateResponse) {
        self.result            = result
        self.title             = result.title
        self.description       = result.description
        self.tags              = result.tags
        self.materials         = result.materials
        self.price             = result.price
        self.personalizationInstructions = result.personalizationInstructions
        self.shippingProfileId = result.shippingProfiles.first?.id
    }

    var canPublish: Bool {
        (result.taxonomyId ?? 0) > 0 && !title.isEmpty && !description.isEmpty && price > 0
    }

    func publish() async {
        guard let taxonomyId = result.taxonomyId, taxonomyId > 0 else {
            error = String(localized: "Etsy category could not be determined. Publish from the web app instead.")
            return
        }
        isPublishing = true
        error = nil
        successMessage = nil
        let req = PublishRequest(
            title:             title,
            description:       description,
            tags:              tags,
            materials:         materials,
            price:             price,
            taxonomyId:        taxonomyId,
            shippingProfileId: shippingProfileId,
            personalizationInstructions: personalizationInstructions,
            imagePreviews:     result.imagePreviews
        )
        do {
            let resp = try await APIClient.shared.publish(req)
            if resp.success {
                publishedListingId = resp.listingId
                withAnimation { successMessage = String(localized: "Listing published on Etsy! 🎉") }
                Haptics.success()
            }
        } catch {
            self.error = error.localizedDescription
            Haptics.error()
        }
        isPublishing = false
    }

    func improve(action: ImproveAction, lang: String) async {
        isImproving = true
        error = nil
        do {
            let resp = try await APIClient.shared.improveListing(
                action: action, lang: lang,
                title: title, description: description, tags: tags,
                materials: materials, price: String(price),
                category: result.taxonomyPath ?? ""
            )
            withAnimation {
                if let t = resp.title, !t.isEmpty             { title = t }
                if let d = resp.description, !d.isEmpty       { description = d }
                if let tg = resp.tags, !tg.isEmpty            { tags = tg }
                successMessage = String(localized: "Listing improved.")
            }
            Haptics.success()
        } catch NetworkError.premiumRequired {
            needsUpgrade = true
        } catch {
            self.error = error.localizedDescription
            Haptics.error()
        }
        isImproving = false
    }

    func applyTranslation(_ resp: TranslateResponse) {
        withAnimation {
            title       = resp.title
            description = resp.description
            tags        = resp.tags
        }
    }
}
