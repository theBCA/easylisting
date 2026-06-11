import SwiftUI

// MARK: - Haptics

enum Haptics {
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    static func error()   { UINotificationFeedbackGenerator().notificationOccurred(.error) }
    static func tap()     { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
}

// MARK: - Buttons

private struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.9 : 1)
            .animation(.spring(duration: 0.2), value: configuration.isPressed)
    }
}

struct PrimaryButton: View {
    let title: LocalizedStringKey
    let isLoading: Bool
    let action: () -> Void

    init(_ title: LocalizedStringKey, isLoading: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.isLoading = isLoading
        self.action = action
    }

    init(verbatim title: String, isLoading: Bool = false, action: @escaping () -> Void) {
        self.title = LocalizedStringKey(title)
        self.isLoading = isLoading
        self.action = action
    }

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            HStack(spacing: 8) {
                if isLoading {
                    ProgressView().tint(.white)
                }
                Text(title)
                    .font(.system(size: 16, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Theme.purpleGradient)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: Theme.purple.opacity(0.35), radius: 10, y: 4)
        }
        .buttonStyle(PressableStyle())
        .disabled(isLoading)
    }
}

struct SecondaryButton: View {
    let title: LocalizedStringKey
    let action: () -> Void

    init(_ title: LocalizedStringKey, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button {
            Haptics.tap()
            action()
        } label: {
            Text(title)
                .font(.system(size: 16, weight: .semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(Theme.purpleLight)
                .foregroundStyle(Theme.purple)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(PressableStyle())
    }
}

// MARK: - Banners

struct ErrorBanner: View {
    let message: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.circle.fill")
            Text(message)
                .font(.subheadline)
        }
        .foregroundStyle(.white)
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.gradient)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

struct SuccessBanner: View {
    let message: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
            Text(message)
                .font(.subheadline)
        }
        .foregroundStyle(.white)
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.green.gradient)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

// MARK: - Cards & fields

struct SectionCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding()
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
            .shadow(color: Theme.cardShadow, radius: Theme.cardShadowRadius, y: 2)
    }
}

struct LabeledField: View {
    let label: LocalizedStringKey
    @Binding var text: String
    var axis: Axis = .horizontal
    var minLines: Int = 1

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundStyle(Theme.textSecondary)
                .textCase(.uppercase)
            if axis == .vertical {
                TextEditor(text: $text)
                    .frame(minHeight: CGFloat(minLines) * 22)
                    .scrollContentBackground(.hidden)
            } else {
                TextField(label, text: $text)
            }
        }
    }
}
