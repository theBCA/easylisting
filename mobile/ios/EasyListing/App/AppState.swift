import SwiftUI

@Observable
@MainActor
final class AppState {
    var isAuthenticated = false
    var isGuest         = false
    var isEmailVerified = false
    var isPremium       = false
    var shopName: String?
    var remaining       = 0
    var trendyolConnected = false
    var isLoading       = true

    static let shared = AppState()
    private init() {}

    @MainActor
    func refresh() async {
        #if DEBUG
        if SnapshotData.isActive {
            SnapshotData.applyAppState(to: self)
            return
        }
        #endif
        guard APIClient.shared.mobileToken != nil else {
            isAuthenticated = false
            isLoading = false
            return
        }
        do {
            let status = try await APIClient.shared.fetchStatus()
            remaining         = status.remaining
            isGuest           = status.isGuest
            isEmailVerified   = status.isEmail
            trendyolConnected = status.trendyolConnected
            isPremium         = status.remaining >= 999
            isAuthenticated   = true
        } catch NetworkError.unauthorized {
            APIClient.shared.mobileToken = nil
            isAuthenticated = false
        } catch {
            // network error — keep previous state
        }
        isLoading = false
    }

    @MainActor
    func login(mobileToken: String, shopName: String?, isGuest: Bool) {
        APIClient.shared.mobileToken = mobileToken
        self.shopName        = shopName
        self.isGuest         = isGuest
        self.isAuthenticated = true
        Task { await refresh() }
    }

    @MainActor
    func logout() async {
        await APIClient.shared.logout()
        isAuthenticated  = false
        isGuest          = false
        isPremium        = false
        shopName         = nil
        remaining        = 0
    }
}
