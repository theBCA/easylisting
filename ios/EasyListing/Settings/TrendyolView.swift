import SwiftUI

struct TrendyolView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var supplierId = ""
    @State private var apiKey     = ""
    @State private var apiSecret  = ""
    @State private var isLoading  = false
    @State private var error: String?
    @State private var success    = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            Form {
                Section {
                    TextField("Supplier ID", text: $supplierId)
                        .keyboardType(.numberPad)
                    SecureField("API Key", text: $apiKey)
                    SecureField("API Secret", text: $apiSecret)
                } header: {
                    Text("Trendyol API Credentials")
                } footer: {
                    Text("Found in Trendyol Seller Panel > API Integration.")
                }

                if let error { Section { ErrorBanner(message: error) } }
                if success   { Section { SuccessBanner(message: String(localized: "Trendyol connected!")) } }

                Section {
                    if appState.trendyolConnected {
                        Button("Disconnect", role: .destructive) {
                            Task { await disconnect() }
                        }
                    } else {
                        PrimaryButton("Connect", isLoading: isLoading) {
                            Task { await connect() }
                        }
                    }
                }
            }
        }
        .navigationTitle("Trendyol")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func connect() async {
        isLoading = true
        error = nil
        do {
            try await APIClient.shared.trendyolConnect(supplierId: supplierId, apiKey: apiKey, apiSecret: apiSecret)
            appState.trendyolConnected = true
            success = true
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    private func disconnect() async {
        do {
            try await APIClient.shared.trendyolDisconnect()
            appState.trendyolConnected = false
        } catch {
            self.error = error.localizedDescription
        }
    }
}
