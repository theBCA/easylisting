import SwiftUI

enum Theme {
    // MARK: - Brand colours
    static let purple      = Color(red: 0x5B/255, green: 0x47/255, blue: 0xE0/255)
    static let purpleDark  = Color(red: 0x4A/255, green: 0x37/255, blue: 0xC9/255)
    static let purpleLight = Color(red: 0xF0/255, green: 0xEE/255, blue: 0xFF/255)
    static let purpleSoft  = Color(red: 0x6B/255, green: 0x5A/255, blue: 0xED/255)
    static let green       = Color(red: 0x05/255, green: 0x96/255, blue: 0x69/255)
    static let amber       = Color(red: 0xF5/255, green: 0x9E/255, blue: 0x0B/255)

    // MARK: - Semantic colours
    static let bg            = Color(light: Color(red: 0xF5/255, green: 0xF6/255, blue: 0xFA/255),
                                     dark:  Color(.systemGroupedBackground))
    static let card          = Color(light: .white,
                                     dark:  Color(.secondarySystemGroupedBackground))
    static let border        = Color(light: Color(red: 0xE8/255, green: 0xEA/255, blue: 0xF2/255),
                                     dark:  Color(.separator))
    static let textPrimary   = Color(.label)
    static let textSecondary = Color(.secondaryLabel)
    static let textTertiary  = Color(.tertiaryLabel)

    // MARK: - Gradients
    static let purpleGradient = LinearGradient(
        colors: [purpleSoft, purple],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    static let heroGradient = LinearGradient(
        colors: [Color(red: 0x7C/255, green: 0x3A/255, blue: 0xED/255),
                 Color(red: 0x5B/255, green: 0x47/255, blue: 0xE0/255),
                 Color(red: 0x43/255, green: 0x61/255, blue: 0xEE/255)],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )

    // MARK: - Shadows
    static let cardShadow        = purple.opacity(0.07)
    static let cardShadowRadius: CGFloat = 14
    static let buttonShadow      = purple.opacity(0.28)
    static let buttonShadowRadius: CGFloat = 14

    // MARK: - Shape & spacing
    static let radius: CGFloat      = 16
    static let radiusSmall: CGFloat = 10
    static let radiusLarge: CGFloat = 24
    enum Space {
        static let xs: CGFloat  = 4
        static let sm: CGFloat  = 8
        static let md: CGFloat  = 16
        static let lg: CGFloat  = 24
        static let xl: CGFloat  = 32
    }
}

extension Color {
    init(light: Color, dark: Color) {
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light) })
    }
}
