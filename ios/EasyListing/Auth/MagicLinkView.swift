import SwiftUI

struct MagicLinkView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var email           = ""
    @State private var marketingConsent = false
    @State private var state: ViewState = .input
    @State private var error: String?

    enum ViewState { case input, sent, verifying }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bg.ignoresSafeArea()

                VStack(spacing: 24) {
                    switch state {
                    case .input:
                        inputView
                    case .sent:
                        sentView
                    case .verifying:
                        ProgressView("Verifying…")
                            .padding()
                    }
                }
                .padding()
            }
            .navigationTitle("Sign in with Email")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .magicLinkReceived)) { note in
            if let token = note.userInfo?["token"] as? String {
                Task { await verifyMagicToken(token) }
            }
        }
    }

    // MARK: - Input

    private var inputView: some View {
        VStack(spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Your Email")
                    .font(.caption.bold())
                    .foregroundStyle(Theme.textSecondary)
                TextField("you@example.com", text: $email)
                    .keyboardType(.emailAddress)
                    .textContentType(.emailAddress)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .padding(12)
                    .background(Theme.card)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            Toggle(isOn: $marketingConsent) {
                Text("Keep me updated")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textSecondary)
            }
            .toggleStyle(.switch)

            if let error {
                ErrorBanner(message: error)
            }

            PrimaryButton("Send Link") {
                Task { await sendMagicLink() }
            }

            Text("We'll send a sign-in link to your email.")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Sent

    private var sentView: some View {
        VStack(spacing: 16) {
            Image(systemName: "envelope.circle.fill")
                .font(.system(size: 64))
                .foregroundStyle(Theme.purple)

            Text("Link Sent")
                .font(.title2.bold())

            Text("We sent a sign-in link to **\(email)**. Tap the link and the app will open automatically.")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)

            Button("Use a different email") {
                state = .input
                email = ""
            }
            .font(.subheadline)
            .foregroundStyle(Theme.purple)
        }
    }

    // MARK: - Actions

    private func sendMagicLink() async {
        error = nil
        let trimmed = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard trimmed.contains("@"), trimmed.contains(".") else {
            error = String(localized: "Enter a valid email address.")
            return
        }
        do {
            let resp = try await APIClient.shared.requestMagicLink(email: trimmed, marketingConsent: marketingConsent)
            if let token = resp.mobileToken {
                // Returning user — already have a mobile token
                appState.login(mobileToken: token, shopName: nil, isGuest: true)
                dismiss()
            } else {
                state = .sent
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func verifyMagicToken(_ token: String) async {
        state = .verifying
        do {
            let resp = try await APIClient.shared.verifyMagicToken(token)
            appState.login(mobileToken: resp.mobileToken, shopName: nil, isGuest: true)
            dismiss()
        } catch {
            self.error = error.localizedDescription
            state = .sent
        }
    }
}
