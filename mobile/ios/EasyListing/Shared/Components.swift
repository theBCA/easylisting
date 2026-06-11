import SwiftUI

// MARK: - Haptics

@MainActor
enum Haptics {
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    static func error()   { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    static func tap()     { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    static func medium()  { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }
}

// MARK: - Press style

struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

// MARK: - Primary button

struct PrimaryButton: View {
    let title: LocalizedStringKey
    let isLoading: Bool
    let action: () -> Void

    init(_ title: LocalizedStringKey, isLoading: Bool = false, action: @escaping () -> Void) {
        self.title = title; self.isLoading = isLoading; self.action = action
    }

    init(verbatim title: String, isLoading: Bool = false, action: @escaping () -> Void) {
        self.title = LocalizedStringKey(title); self.isLoading = isLoading; self.action = action
    }

    var body: some View {
        Button {
            Task { @MainActor in Haptics.tap() }
            action()
        } label: {
            HStack(spacing: 8) {
                if isLoading {
                    ProgressView().tint(.white).scaleEffect(0.85)
                }
                Text(title).font(.system(size: 16, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(isLoading ? AnyShapeStyle(Theme.purple) : AnyShapeStyle(Theme.purpleGradient))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
            .shadow(color: Theme.buttonShadow, radius: Theme.buttonShadowRadius, y: 5)
        }
        .buttonStyle(PressableStyle())
        .disabled(isLoading)
    }
}

// MARK: - Secondary button

struct SecondaryButton: View {
    let title: LocalizedStringKey
    let action: () -> Void

    init(_ title: LocalizedStringKey, action: @escaping () -> Void) {
        self.title = title; self.action = action
    }

    var body: some View {
        Button {
            Task { @MainActor in Haptics.tap() }
            action()
        } label: {
            Text(title)
                .font(.system(size: 16, weight: .semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                .background(Theme.purpleLight)
                .foregroundStyle(Theme.purple)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.radius)
                        .stroke(Theme.purple.opacity(0.18), lineWidth: 1)
                )
        }
        .buttonStyle(PressableStyle())
    }
}

// MARK: - Icon button

struct IconButton: View {
    let icon: String
    let label: LocalizedStringKey
    let action: () -> Void

    var body: some View {
        Button {
            Task { @MainActor in Haptics.tap() }
            action()
        } label: {
            VStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 22, weight: .medium))
                Text(label).font(.caption.weight(.medium))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Theme.card)
            .foregroundStyle(Theme.purple)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).stroke(Theme.border, lineWidth: 1))
            .shadow(color: Theme.cardShadow, radius: 6, y: 2)
        }
        .buttonStyle(PressableStyle())
    }
}

// MARK: - Banners

struct ErrorBanner: View {
    let message: String
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill").font(.title3)
            Text(message).font(.subheadline).fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.9))
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

struct SuccessBanner: View {
    let message: String
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill").font(.title3)
            Text(message).font(.subheadline).fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.green.opacity(0.9))
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

// MARK: - Cards

struct SectionCard<Content: View>: View {
    let title: String?
    let content: Content

    init(title: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let title {
                Text(title)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, Theme.Space.md)
                    .padding(.top, Theme.Space.md)
                    .padding(.bottom, 10)
            }
            content
                .padding(title == nil ? Theme.Space.md : 0)
                .padding(.horizontal, title != nil ? Theme.Space.md : 0)
                .padding(.bottom, title != nil ? Theme.Space.md : 0)
        }
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
        .shadow(color: Theme.cardShadow, radius: Theme.cardShadowRadius, y: 3)
    }
}

// MARK: - Input field

struct InputField: View {
    let label: String
    @Binding var text: String
    var placeholder: String = ""
    var axis: Axis = .horizontal
    var minLines: Int = 1
    var keyboardType: UIKeyboardType = .default

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Theme.textSecondary)
            if axis == .vertical {
                TextEditor(text: $text)
                    .frame(minHeight: CGFloat(minLines) * 24)
                    .scrollContentBackground(.hidden)
                    .font(.body)
            } else {
                TextField(placeholder.isEmpty ? label : placeholder, text: $text)
                    .keyboardType(keyboardType)
                    .font(.body)
            }
        }
    }
}

// Keep legacy LabeledField as alias for compatibility
typealias LabeledField = InputField

// MARK: - Chip / tag pill

struct TagPill: View {
    let text: String
    let onRemove: (() -> Void)?

    init(_ text: String, onRemove: (() -> Void)? = nil) {
        self.text = text; self.onRemove = onRemove
    }

    var body: some View {
        HStack(spacing: 4) {
            Text(text).font(.caption.weight(.medium))
            if let onRemove {
                Button(action: onRemove) {
                    Image(systemName: "xmark").font(.system(size: 9, weight: .bold))
                }
            }
        }
        .padding(.horizontal, 11).padding(.vertical, 6)
        .background(Theme.purpleLight)
        .foregroundStyle(Theme.purple)
        .clipShape(Capsule())
    }
}

// MARK: - Premium badge

struct PremiumBadge: View {
    var body: some View {
        Label("Premium", systemImage: "crown.fill")
            .font(.caption.weight(.semibold))
            .foregroundStyle(Theme.amber)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(Theme.amber.opacity(0.12))
            .clipShape(Capsule())
    }
}

// MARK: - Remaining counter pill

struct RemainingPill: View {
    let count: Int
    var body: some View {
        Text("\(count) left")
            .font(.caption.weight(.semibold))
            .foregroundStyle(count == 0 ? .red : Theme.purple)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background((count == 0 ? Color.red : Theme.purple).opacity(0.1))
            .clipShape(Capsule())
    }
}

// MARK: - Divider

struct SubtleDivider: View {
    var body: some View {
        Rectangle()
            .fill(Theme.border)
            .frame(height: 0.5)
    }
}
