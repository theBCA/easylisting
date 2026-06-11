#if DEBUG
import UIKit
import Foundation

enum SnapshotData {

    // MARK: - Screen routing

    static var screen: String? {
        ProcessInfo.processInfo.environment["SNAPSHOT_SCREEN"]
    }
    static var isActive: Bool { screen != nil }

    // MARK: - App state

    @MainActor
    static func applyAppState(to state: AppState) {
        state.isAuthenticated     = screen != "auth"
        state.isGuest             = false
        state.isEmailVerified     = true
        state.isPremium           = false
        state.shopName            = "MyShop"
        state.remaining           = 5
        state.trendyolConnected   = false
        state.isLoading           = false
    }

    // MARK: - GenerateResponse

    static let mockResult: GenerateResponse = {
        let json = """
        {
          "title": "Handmade Ceramic Coffee Mug | Vintage Glaze | 350ml Tea Cup | Pottery Gift for Her",
          "description": "Bring warmth to your morning routine with this beautifully handcrafted ceramic coffee mug. Each piece is wheel-thrown and finished with a one-of-a-kind vintage glaze, making no two mugs exactly alike.\\n\\nPerfect for coffee, tea, or hot chocolate — the generous 350ml capacity holds just the right amount.\\n\\n✦ Microwave and dishwasher safe\\n✦ Food-grade glaze, lead-free\\n✦ Ships in a gift-ready box\\n\\nA thoughtful gift for the coffee lover in your life.",
          "tags": ["ceramic mug", "handmade pottery", "coffee gift", "vintage mug", "tea cup", "pottery mug", "handcrafted", "kitchen decor", "gift for her", "stoneware mug", "unique mug", "artisan pottery", "350ml mug"],
          "materials": ["stoneware clay", "food-grade glaze"],
          "suggested_price_eur": 24.5,
          "taxonomy_id": 1,
          "taxonomy_path": "Home & Living > Kitchen & Dining > Drink & Barware > Mugs",
          "shipping_profiles": [{"shipping_profile_id": 1, "title": "Standard Shipping"}],
          "image_previews": []
        }
        """
        return try! JSONDecoder().decode(GenerateResponse.self, from: Data(json.utf8))
    }()

    // MARK: - Mock listings

    static let mockListings: [EtsyListing] = {
        let json = """
        [
          {
            "listing_id": 1001,
            "title": "Handmade Ceramic Coffee Mug | Vintage Glaze | 350ml",
            "description": "Beautifully handcrafted ceramic mug.",
            "price": {"amount": 2450, "divisor": 100, "currency_code": "EUR"},
            "state": "active",
            "images": [],
            "tags": ["ceramic mug", "handmade"]
          },
          {
            "listing_id": 1002,
            "title": "Macramé Wall Hanging | Boho Home Decor | Large 60cm",
            "description": "Stunning woven wall art.",
            "price": {"amount": 3800, "divisor": 100, "currency_code": "EUR"},
            "state": "active",
            "images": [],
            "tags": ["macrame", "wall art"]
          },
          {
            "listing_id": 1003,
            "title": "Soy Wax Candle | Lavender & Cedar | Hand-Poured",
            "description": "Relaxing aromatherapy candle.",
            "price": {"amount": 1850, "divisor": 100, "currency_code": "EUR"},
            "state": "active",
            "images": [],
            "tags": ["candle", "soy wax"]
          }
        ]
        """
        return try! JSONDecoder().decode([EtsyListing].self, from: Data(json.utf8))
    }()

    // MARK: - Sample product image

    /// A programmatic placeholder that looks like a product photo.
    static let sampleProductImage: UIImage = {
        let size = CGSize(width: 400, height: 400)
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { ctx in
            // Warm cream background
            UIColor(red: 0.96, green: 0.93, blue: 0.88, alpha: 1).setFill()
            ctx.fill(CGRect(origin: .zero, size: size))

            // Soft inner shadow gradient
            let colors = [UIColor(red: 0.85, green: 0.80, blue: 0.72, alpha: 0.4).cgColor,
                          UIColor.clear.cgColor]
            let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                      colors: colors as CFArray,
                                      locations: [0, 1])!
            ctx.cgContext.drawRadialGradient(gradient,
                startCenter: CGPoint(x: 200, y: 200), startRadius: 180,
                endCenter:   CGPoint(x: 200, y: 200), endRadius:   10,
                options: [])

            // Draw cup icon
            let config = UIImage.SymbolConfiguration(pointSize: 140, weight: .thin)
            if let icon = UIImage(systemName: "cup.and.saucer.fill", withConfiguration: config)?
                .withTintColor(UIColor(red: 0.55, green: 0.40, blue: 0.30, alpha: 0.8),
                               renderingMode: .alwaysOriginal) {
                let iconSize = icon.size
                let origin = CGPoint(x: (size.width  - iconSize.width)  / 2,
                                     y: (size.height - iconSize.height) / 2 - 10)
                icon.draw(at: origin)
            }
        }
    }()
}
#endif
