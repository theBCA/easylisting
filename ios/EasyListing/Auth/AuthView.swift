import SwiftUI
import AuthenticationServices

struct AuthView: View {
    @Environment(AppState.self) private var appState
    @State private var showMagicLink = false
    @State private var isLoading     = false
    @State private var error: String?
    @State private var oauth = OAuthCoordinator()

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()

            ScrollView {
                VStack(spacing: 32) {
                    // Logo
                    VStack(spacing: 8) {
                        Image(systemName: "tag.fill")
                            .font(.system(size: 72))
                            .foregroundStyle(Theme.purpleGradient)
                        HStack(spacing: 0) {
                            Text(verbatim: "Easy").font(.largeTitle.bold()).foregroundStyle(Theme.purple)
                            Text(verbatim: "Listing").font(.largeTitle.bold()).foregroundStyle(Theme.textPrimary)
                        }
                        Text("Create Etsy listings with AI")
                            .font(.subheadline)
                            .foregroundStyle(Theme.textSecondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding(.top, 60)

                    if let error {
                        ErrorBanner(message: error)
                            .padding(.horizontal)
                    }

                    VStack(spacing: 12) {
                        PrimaryButton("Connect with Etsy", isLoading: isLoading) {
                            Task { await startEtsyOAuth() }
                        }
                        .padding(.horizontal)

                        SecondaryButton("Sign in with Email") {
                            showMagicLink = true
                        }
                        .padding(.horizontal)

                        Button("Continue as guest") {
                            Task { await startGuest() }
                        }
                        .font(.subheadline)
                        .foregroundStyle(Theme.textSecondary)
                    }

                    Text("By continuing you accept the [Terms of Service](https://easylisting.app/terms) and [Privacy Policy](https://easylisting.app/privacy).")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                        .padding(.bottom, 32)
                }
            }
        }
        .sheet(isPresented: $showMagicLink) {
            MagicLinkView()
        }
    }

    // MARK: - Etsy OAuth

    private func startEtsyOAuth() async {
        isLoading = true
        error = nil
        defer { isLoading = false }

        do {
            let callbackURL = try await oauth.authenticate(
                startURL: URL(string: "\(Config.baseURL)/auth/start?mobile=1")!,
                callbackScheme: "easylisting"
            )
            handleOAuthCallback(callbackURL)
        } catch let err as ASWebAuthenticationSessionError where err.code == .canceledLogin {
            // user dismissed the sheet — not an error
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
        isLoading = true
        error = nil
        defer { isLoading = false }
        do {
            let token = try await APIClient.shared.guestLogin()
            appState.login(mobileToken: token, shopName: nil, isGuest: true)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - OAuth coordinator
// ASWebAuthenticationSession requires a presentation anchor; without one it
// fails to start on a real device. This coordinator provides the key window
// and keeps the session alive for the duration of the flow.

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
                if let error {
                    continuation.resume(throwing: error)
                } else if let callbackURL {
                    continuation.resume(returning: callbackURL)
                } else {
                    continuation.resume(throwing: NetworkError.noData)
                }
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
