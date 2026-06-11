import SwiftUI
import PhotosUI

// Bulk creation: each product needs its own photos + hint; the backend
// generates one listing per request, so products are processed sequentially.
// Premium feature — backend enforces premium on unlimited usage.

struct BulkProduct: Identifiable {
    let id = UUID()
    var images: [UIImage] = []
    var hint = ""
    var result: GenerateResponse?
    var error: String?
    var state: State = .pending

    enum State { case pending, generating, done, failed }
}

struct BulkView: View {
    @Environment(AppState.self) private var appState
    @State private var products: [BulkProduct] = [BulkProduct()]
    @State private var lang     = Locale.current.language.languageCode?.identifier == "tr" ? "tr" : "en"
    @State private var platform = "etsy"
    @State private var isRunning = false
    @State private var showUpgrade = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 16) {
                    if !appState.isPremium {
                        SectionCard {
                            HStack(spacing: 10) {
                                Image(systemName: "crown.fill").foregroundStyle(.orange)
                                Text("Bulk creation works best with Premium — free plans are limited.")
                                    .font(.caption)
                                    .foregroundStyle(Theme.textSecondary)
                                Spacer()
                            }
                        }
                        .padding(.horizontal)
                    }

                    ForEach($products) { $product in
                        BulkProductCard(product: $product) {
                            withAnimation { products.removeAll { $0.id == product.id } }
                        }
                        .padding(.horizontal)
                    }

                    if products.count < 10 {
                        Button {
                            withAnimation { products.append(BulkProduct()) }
                        } label: {
                            Label("Add Product", systemImage: "plus.circle")
                                .font(.subheadline.bold())
                                .foregroundStyle(Theme.purple)
                        }
                    }

                    SectionCard {
                        HStack {
                            Picker("Language", selection: $lang) {
                                Text(verbatim: "English").tag("en")
                                Text(verbatim: "Türkçe").tag("tr")
                                Text(verbatim: "Deutsch").tag("de")
                            }
                            .pickerStyle(.menu)
                            Divider()
                            Picker("Platform", selection: $platform) {
                                Text(verbatim: "Etsy").tag("etsy")
                                Text(verbatim: "Trendyol").tag("trendyol")
                            }
                            .pickerStyle(.menu)
                        }
                    }
                    .padding(.horizontal)

                    PrimaryButton(isRunning ? "Generating…" : "Bulk Generate", isLoading: isRunning) {
                        Task { await runBulk() }
                    }
                    .padding(.horizontal)
                    .disabled(!hasReadyProducts)
                    .opacity(hasReadyProducts ? 1 : 0.5)
                }
                .padding(.vertical)
            }
        }
        .navigationTitle("Bulk Generate")
        .navigationBarTitleDisplayMode(.large)
        .sheet(isPresented: $showUpgrade) { UpgradeView() }
    }

    private var hasReadyProducts: Bool {
        !isRunning && products.contains { !$0.images.isEmpty && $0.state == .pending }
    }

    private func runBulk() async {
        isRunning = true
        for i in products.indices {
            guard products[i].state == .pending, !products[i].images.isEmpty else { continue }
            products[i].state = .generating
            do {
                let result = try await APIClient.shared.generate(
                    images: products[i].images,
                    hint:   products[i].hint,
                    lang:   lang,
                    platform: platform
                )
                products[i].result = result
                products[i].state  = .done
                Haptics.success()
            } catch NetworkError.limitReached {
                products[i].state = .failed
                products[i].error = NetworkError.limitReached.errorDescription
                showUpgrade = true
                break
            } catch {
                products[i].state = .failed
                products[i].error = error.localizedDescription
            }
        }
        await appState.refresh()
        isRunning = false
    }
}

// MARK: - Product card

struct BulkProductCard: View {
    @Binding var product: BulkProduct
    let onRemove: () -> Void
    @State private var pickerItems: [PhotosPickerItem] = []

    var body: some View {
        SectionCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    statusIcon
                    TextField("Product hint…", text: $product.hint)
                        .disabled(product.state != .pending)
                    Spacer()
                    if product.state == .pending {
                        Button(action: onRemove) {
                            Image(systemName: "minus.circle.fill").foregroundStyle(.red.opacity(0.7))
                        }
                    }
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        if product.images.count < 5, product.state == .pending {
                            PhotosPicker(selection: $pickerItems,
                                         maxSelectionCount: 5 - product.images.count,
                                         matching: .images) {
                                RoundedRectangle(cornerRadius: 8)
                                    .stroke(Theme.purple, style: StrokeStyle(lineWidth: 1.5, dash: [5]))
                                    .frame(width: 56, height: 56)
                                    .overlay { Image(systemName: "plus").foregroundStyle(Theme.purple) }
                            }
                        }
                        ForEach(Array(product.images.enumerated()), id: \.offset) { i, img in
                            ZStack(alignment: .topTrailing) {
                                Image(uiImage: img)
                                    .resizable().scaledToFill()
                                    .frame(width: 56, height: 56)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                if product.state == .pending {
                                    Button {
                                        _ = product.images.remove(at: i)
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .font(.caption)
                                            .foregroundStyle(.white, .black.opacity(0.6))
                                    }
                                }
                            }
                        }
                    }
                }

                if let error = product.error {
                    Text(error).font(.caption).foregroundStyle(.red)
                }

                if let result = product.result {
                    NavigationLink(destination: ResultView(result: result)) {
                        HStack {
                            Text(result.title)
                                .font(.caption.bold())
                                .foregroundStyle(Theme.purple)
                                .lineLimit(1)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption2)
                                .foregroundStyle(Theme.textSecondary)
                        }
                        .padding(8)
                        .background(Theme.purpleLight)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
        .onChange(of: pickerItems) { _, items in
            guard !items.isEmpty else { return }
            Task {
                for item in items {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let img  = UIImage(data: data),
                       product.images.count < 5 {
                        product.images.append(img)
                    }
                }
                pickerItems = []
            }
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch product.state {
        case .pending:    Image(systemName: "circle.dashed").foregroundStyle(Theme.textSecondary)
        case .generating: ProgressView().controlSize(.small)
        case .done:       Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed:     Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}
