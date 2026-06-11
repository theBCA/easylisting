import SwiftUI

// Mirrors the web design system (templates/index.html :root):
// --primary:#5B47E0 --primary-dark:#4A37C9 --primary-light:#F0EEFF
// --bg:#F6F7FB --card:#FFF --border:#E5E7F0 --text:#111827 --muted:#6B7280
// button gradient: linear-gradient(135deg,#6B5AED,#5B47E0)
// shadow: 0 2px 16px rgba(91,71,224,.09)

enum Theme {
    static let purple       = Color(red: 0x5B/255, green: 0x47/255, blue: 0xE0/255)
    static let purpleDark   = Color(red: 0x4A/255, green: 0x37/255, blue: 0xC9/255)
    static let purpleLight  = Color(red: 0xF0/255, green: 0xEE/255, blue: 0xFF/255)
    static let purpleSoft   = Color(red: 0x6B/255, green: 0x5A/255, blue: 0xED/255)
    static let green        = Color(red: 0x05/255, green: 0x96/255, blue: 0x69/255)

    static let bg            = Color(light: Color(red: 0xF6/255, green: 0xF7/255, blue: 0xFB/255),
                                     dark: Color(.systemGroupedBackground))
    static let card          = Color(light: .white,
                                     dark: Color(.secondarySystemGroupedBackground))
    static let border        = Color(light: Color(red: 0xE5/255, green: 0xE7/255, blue: 0xF0/255),
                                     dark: Color(.separator))
    static let textPrimary   = Color(.label)
    static let textSecondary = Color(.secondaryLabel)

    static let purpleGradient = LinearGradient(
        colors: [purpleSoft, purple],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )

    static let cardShadow       = purple.opacity(0.09)
    static let cardShadowRadius: CGFloat = 8
    static let radius: CGFloat  = 14
}

private extension Color {
    init(light: Color, dark: Color) {
        self.init(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
    }
}
