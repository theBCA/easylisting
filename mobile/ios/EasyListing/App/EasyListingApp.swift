import SwiftUI

@main
struct EasyListingApp: App {
    @State private var appState = AppState.shared

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .onOpenURL { url in
                    handleDeepLink(url)
                }
        }
    }

    private func handleDeepLink(_ url: URL) {
        guard url.scheme == "easylisting" else { return }
        let host = url.host ?? ""
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let params = Dictionary(
            uniqueKeysWithValues: (components?.queryItems ?? []).compactMap { item in
                item.value.map { (item.name, $0) }
            }
        )

        switch host {
        case "auth":
            if url.path.hasPrefix("/magic") {
                // Magic link: easylisting://auth/magic?token=xxx
                if let token = params["token"] {
                    NotificationCenter.default.post(name: .magicLinkReceived, object: nil, userInfo: ["token": token])
                }
            } else if let token = params["mobile_token"] {
                // Etsy OAuth callback: easylisting://auth?mobile_token=xxx&shop_name=yyy
                let name = params["shop_name"]
                Task { @MainActor in
                    appState.login(mobileToken: token, shopName: name, isGuest: false)
                }
            }
        case "upgrade":
            if url.path.hasPrefix("/success") {
                // Stripe payment complete: easylisting://upgrade/success?plan=xxx
                NotificationCenter.default.post(name: .upgradeSuccess, object: nil)
                Task { @MainActor in await appState.refresh() }
            }
        default:
            break
        }
    }
}

extension Notification.Name {
    static let magicLinkReceived = Notification.Name("magicLinkReceived")
    static let upgradeSuccess    = Notification.Name("upgradeSuccess")
}

// MARK: - Root view

struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Group {
            if appState.isLoading {
                SplashView()
            } else if appState.isAuthenticated {
                MainTabView()
            } else {
                AuthView()
            }
        }
        .task {
            await appState.refresh()
        }
    }
}

// MARK: - Splash

struct SplashView: View {
    var body: some View {
        ZStack {
            Theme.purple.ignoresSafeArea()
            VStack(spacing: 12) {
                Image(systemName: "tag.fill")
                    .font(.system(size: 60))
                    .foregroundStyle(.white)
                Text("EasyListing")
                    .font(.largeTitle.bold())
                    .foregroundStyle(.white)
            }
        }
    }
}

// MARK: - Main tab view

struct MainTabView: View {
    var body: some View {
        TabView {
            NavigationStack { GenerateView() }
                .tabItem { Label("Create", systemImage: "sparkles") }

            NavigationStack { ListingsView() }
                .tabItem { Label("My Listings", systemImage: "list.bullet") }

            NavigationStack { SettingsView() }
                .tabItem { Label("Settings", systemImage: "gear") }
        }
        .tint(Theme.purple)
    }
}
