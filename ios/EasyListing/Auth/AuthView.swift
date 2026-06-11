import SwiftUI
import AuthenticationServices

struct AuthView: View {
    @Environment(AppState.self) private var appState
    @State private var showMagicLink = false
    @State private var isLoadingEtsy  = false
    @State private var isLoadingGuest = false
    @State private var error: String?
    @State private var oauth = OAuthCoordinator()

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                // ── Hero background ──────────────────────────────────
                Theme.heroGradient
                    .ignoresSafeArea()

                // ── Hero content ─────────────────────────────────────
                VStack(spacing: 0) {
                    Spacer()
                    VStack(spacing: Theme.Space.lg) {
                        // Logo mark
                        ZStack {
                            Circle()
                                .fill(.white.opacity(0.15))
                                .frame(width: 100, height: 100)
                            Image(systemName: "tag.fill")
                                .font(.system(size: 44))
                                .foregroundStyle(.white)
                        }

                        VStack(spacing: 6) {
                            HStack(spacing: 0) {
                                Text(verbatim: "Easy")
                                    .font(.system(size: 38, weight: .black))
                                    .foregroundStyle(.white)
                                Text(verbatim: "Listing")
                                    .font(.system(size: 38, weight: .light))
                                    .foregroundStyle(.white.opacity(0.9))
                            }
                            Text("AI-powered Etsy listing creator")
                                .font(.subheadline)
                                .foregroundStyle(.white.opacity(0.75))
                        }

                        // Feature bullets
                        VStack(spacing: 10) {
                            HeroBullet(icon: "sparkles",       text: "Generate titles, descriptions & tags")
                            HeroBullet(icon: "arrow.up.circle", text: "Publish directly to your Etsy shop")
                            HeroBullet(icon: "globe",           text: "Multi-language support")
                        }
                        .padding(.horizontal, Theme.Space.xl)
                    }
                    .padding(.bottom, 280)
                    Spacer()
                }

                // ── Action card ──────────────────────────────────────
                VStack(spacing: 0) {
                    // Pull indicator
                    Capsule()
                        .fill(.white.opacity(0.3))
                        .frame(width: 36, height: 4)
                        .padding(.top, 12)
                        .padding(.bottom, 20)

                    VStack(spacing: 12) {
                        if let error {
                            ErrorBanner(message: error)
                                .padding(.bottom, 4)
                                .transition(.move(edge: .top).combined(with: .opacity))
                        }

                        PrimaryButton("Connect with Etsy", isLoading: isLoadingEtsy) {
                            Task { await startEtsyOAuth() }
                        }

                        SecondaryButton("Sign in with Email") {
                            showMagicLink = true
                        }

                        Button {
                            Task { await startGuest() }
                        } label: {
                            HStack(spacing: 6) {
                                if isLoadingGuest {
                                    ProgressView().scaleEffect(0.7)
                                        .tint(Theme.textSecondary)
                                }
                                Text("Continue as guest")
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(Theme.textSecondary)
                            }
                            .frame(height: 44)
                        }
                        .disabled(isLoadingGuest)
                    }
                    .padding(.horizontal, Theme.Space.md)

                    Text("By continuing you accept the [Terms of Service](https://easylisting.app/terms) and [Privacy Policy](https://easylisting.app/privacy).")
                        .font(.caption)
                        .foregroundStyle(Theme.textTertiary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Theme.Space.lg)
                        .padding(.top, 16)
                        .padding(.bottom, max(geo.safeAreaInsets.bottom + 8, 24))
                }
                .background(Theme.bg)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radiusLarge))
                .shadow(color: .black.opacity(0.18), radius: 30, y: -8)
            }
        }
        .ignoresSafeArea()
        .animation(.easeInOut(duration: 0.2), value: error)
        .sheet(isPresented: $showMagicLink) { MagicLinkView() }
    }

    // MARK: - Etsy OAuth

    private func startEtsyOAuth() async {
        isLoadingEtsy = true
        error = nil
        defer { isLoadingEtsy = false }
        do {
            let url = try await oauth.authenticate(
                startURL: URL(string: "\(Config.baseURL)/auth/start?mobile=1")!,
                callbackScheme: "easylisting"
            )
            handleOAuthCallback(url)
        } catch let err as ASWebAuthenticationSessionError where err.code == .canceledLogin {
            // user dismissed — not an error
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func handleOAuthCallback(_ url: URL) {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let token = components.queryItems?.first(where: { $0.name == "mobile_token" })?.value else {
            error = String(localized: "Sign-in could not be completed. Please try again.")
            return
        }
        let shopName = components.queryItems?.first(where: { $0.name == "shop_name" })?.value
        appState.login(mobileToken: token, shopName: shopName, isGuest: false)
    }

    // MARK: - Guest

    private func startGuest() async {
        isLoadingGuest = true
        error = nil
        defer { isLoadingGuest = false }
        do {
            let token = try await APIClient.shared.guestLogin()
            appState.login(mobileToken: token, shopName: nil, isGuest: true)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Hero bullet

private struct HeroBullet: View {
    let icon: String
    let text: String
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .frame(width: 28, height: 28)
                .background(.white.opacity(0.2))
                .clipShape(RoundedRectangle(cornerRadius: 7))
            Text(text)
                .font(.subheadline)
            Spacer()
        }
        .foregroundStyle(.white.opacity(0.9))
    }
}

// MARK: - OAuth coordinator

@MainActor
final class OAuthCoordinator: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var activeSession: ASWebAuthenticationSession?

    func authenticate(startURL: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: startURL,
                callbackURLScheme: callbackScheme
            ) { [weak self] callbackURL, error in
                self?.activeSession = nil
                if let error { continuation.resume(throwing: error) }
                else if let callbackURL { continuation.resume(returning: callbackURL) }
                else { continuation.resume(throwing: NetworkError.noData) }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            activeSession = session
            if !session.start() {
                activeSession = nil
                continuation.resume(throwing: NetworkError.serverError(
                    String(localized: "Could not open the sign-in window.")
                ))
            }
        }
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .flatMap(\.windows)
                .first(where: \.isKeyWindow) ?? ASPresentationAnchor()
        }
    }
}
