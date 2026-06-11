import SwiftUI

@Observable
@MainActor
final class GenerateViewModel {
    var selectedImages: [UIImage] = []
    var hint        = ""
    var lang        = Locale.current.language.languageCode?.identifier == "tr" ? "tr" : "en"
    var platform    = "etsy"
    var isGenerating = false
    var error: String?
    var result: GenerateResponse?
    var needsEmailVerification = false
    var limitReached = false

    func generate() async {
        guard !selectedImages.isEmpty else { return }
        isGenerating = true
        error = nil
        result = nil
        do {
            result = try await APIClient.shared.generate(
                images: selectedImages,
                hint: hint,
                lang: lang,
                platform: platform
            )
            Haptics.success()
        } catch NetworkError.emailVerificationRequired {
            needsEmailVerification = true
        } catch NetworkError.limitReached {
            limitReached = true
        } catch {
            self.error = error.localizedDescription
            Haptics.error()
        }
        isGenerating = false
    }

    func reset() {
        selectedImages = []
        hint = ""
        result = nil
        error = nil
    }
}
