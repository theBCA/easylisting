import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @State private var showLogoutAlert = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            List {
                // Account section
                Section("Account") {
                    HStack {
                        Image(systemName: appState.isGuest ? "person.badge.clock" : "storefront")
                            .foregroundStyle(Theme.purple)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(appState.shopName ?? (appState.isGuest ? String(localized: "Guest") : String(localized: "Connected Account")))
                                .font(.subheadline.bold())
                            Text(appState.isPremium ? String(localized: "Premium") : String(localized: "\(appState.remaining) generations left"))
                                .font(.caption)
                                .foregroundStyle(Theme.textSecondary)
                        }
                    }

                    if appState.isPremium {
                        Label("Premium Member", systemImage: "crown.fill")
                            .foregroundStyle(.orange)
                    } else {
                        NavigationLink {
                            UpgradeView()
                        } label: {
                            Label("Upgrade to Premium", systemImage: "crown")
                                .foregroundStyle(Theme.purple)
                        }
                    }
                }

                // Features section
                Section("Features") {
                    NavigationLink {
                        BulkView()
                    } label: {
                        Label("Bulk Create", systemImage: "square.stack.3d.up")
                    }

                    NavigationLink {
                        TrendyolView()
                    } label: {
                        HStack {
                            Label("Trendyol Connection", systemImage: "t.circle")
                            Spacer()
                            if appState.trendyolConnected {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).font(.caption)
                            }
                        }
                    }
                }

                // App section
                Section("App") {
                    Link(destination: URL(string: "https://easylisting.app/privacy")!) {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                    Link(destination: URL(string: "https://easylisting.app/terms")!) {
                        Label("Terms of Service", systemImage: "doc.text")
                    }
                    Link(destination: URL(string: "mailto:info@kolaylistele.com")!) {
                        Label("Support", systemImage: "envelope")
                    }
                }

                // Logout
                Section {
                    Button(role: .destructive) {
                        showLogoutAlert = true
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.large)
        .alert("Sign Out", isPresented: $showLogoutAlert) {
            Button("Sign Out", role: .destructive) {
                Task { await appState.logout() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("You will be signed out of your account.")
        }
    }
}

// MARK: - Upgrade view

struct UpgradeView: View {
    @Environment(AppState.self) private var appState
    @State private var selectedPlan: UpgradePlan = .starter
    @State private var isLoading = false
    @State private var error: String?
    @State private var showSuccess = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 24) {
                    Image(systemName: "crown.fill")
                        .font(.system(size: 64))
                        .foregroundStyle(.orange)
                        .padding(.top, 40)

                    Text("Upgrade to Premium")
                        .font(.largeTitle.bold())

                    // Feature list
                    VStack(spacing: 12) {
                        FeatureRow(icon: "infinity",       text: String(localized: "Unlimited listing generation"))
                        FeatureRow(icon: "wand.and.stars", text: String(localized: "AI improvement & translate"))
                        FeatureRow(icon: "photo.stack",    text: String(localized: "AI photo variants (Pro)"))
                        FeatureRow(icon: "globe",          text: String(localized: "Multi-language support"))
                        FeatureRow(icon: "arrow.up.doc",   text: String(localized: "Bulk create"))
                    }
                    .padding()
                    .background(Theme.card)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .shadow(color: Theme.cardShadow, radius: Theme.cardShadowRadius)
                    .padding(.horizontal)

                    // Plan picker
                    VStack(spacing: 10) {
                        ForEach(UpgradePlan.allCases) { plan in
                            PlanCard(plan: plan, isSelected: selectedPlan == plan) {
                                selectedPlan = plan
                            }
                        }
                    }
                    .padding(.horizontal)

                    if let error {
                        ErrorBanner(message: error).padding(.horizontal)
                    }

                    if showSuccess {
                        SuccessBanner(message: String(localized: "Payment complete! Your account has been upgraded."))
                            .padding(.horizontal)
                    }

                    if appState.isGuest && !appState.isEmailVerified {
                        SectionCard {
                            VStack(spacing: 8) {
                                Image(systemName: "info.circle").foregroundStyle(Theme.purple)
                                Text("Connect with Etsy or verify your email to upgrade.")
                                    .font(.subheadline)
                                    .multilineTextAlignment(.center)
                                    .foregroundStyle(Theme.textSecondary)
                            }
                            .padding()
                        }
                        .padding(.horizontal)
                    } else {
                        PrimaryButton(verbatim: "\(String(localized: "Upgrade")) — \(selectedPlan.priceLabel)", isLoading: isLoading) {
                            Task { await startCheckout() }
                        }
                        .padding(.horizontal)
                    }

                    Text("Payment processed securely via Stripe. Cancel any time.")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                        .padding(.bottom, 32)
                }
            }
        }
        .navigationTitle("Premium")
        .navigationBarTitleDisplayMode(.inline)
        .onReceive(NotificationCenter.default.publisher(for: .upgradeSuccess)) { _ in
            showSuccess = true
            Task { await appState.refresh() }
        }
    }

    private func startCheckout() async {
        isLoading = true
        error = nil
        defer { isLoading = false }
        do {
            let url = try await APIClient.shared.stripeCheckoutURL(plan: selectedPlan.rawValue)
            await UIApplication.shared.open(url)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Plan models

enum UpgradePlan: String, CaseIterable, Identifiable {
    case starter, pro

    var id: String { rawValue }

    var title: String {
        switch self {
        case .starter: return String(localized: "Starter")
        case .pro:     return String(localized: "Pro")
        }
    }

    var priceLabel: String {
        switch self {
        case .starter: return "$9 / mo"
        case .pro:     return "$19 / mo"
        }
    }

    var description: String {
        switch self {
        case .starter: return String(localized: "Unlimited generations, improve & translate")
        case .pro:     return String(localized: "Everything + AI photo variants")
        }
    }
}

struct PlanCard: View {
    let plan: UpgradePlan
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Theme.purple : Theme.textSecondary)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text(plan.title).font(.subheadline.bold())
                    Text(plan.description).font(.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                Text(plan.priceLabel)
                    .font(.subheadline.bold())
                    .foregroundStyle(Theme.purple)
            }
            .padding()
            .background(isSelected ? Theme.purpleLight : Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radius)
                    .stroke(isSelected ? Theme.purple : Theme.border, lineWidth: isSelected ? 2 : 1)
            )
        }
        .buttonStyle(.plain)
    }
}

struct FeatureRow: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(Theme.purple)
                .frame(width: 24)
            Text(text).font(.subheadline)
            Spacer()
            Image(systemName: "checkmark").foregroundStyle(.green).font(.caption)
        }
    }
}

// MARK: - Safari wrapper

import SafariServices

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }
    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}
