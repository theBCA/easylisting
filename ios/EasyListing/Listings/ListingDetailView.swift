import SwiftUI

struct ListingDetailView: View {
    let listing: EtsyListing

    @State private var title: String
    @State private var description: String
    @State private var tags: [String]

    @State private var isImproving   = false
    @State private var showTranslate = false
    @State private var showUpgrade   = false
    @State private var error: String?
    @State private var successMessage: String?

    init(listing: EtsyListing) {
        self.listing  = listing
        _title        = State(initialValue: listing.title)
        _description  = State(initialValue: listing.description ?? "")
        _tags         = State(initialValue: listing.tags ?? [])
    }

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 16) {
                    if let imgURL = listing.images?.first?.url570xN.flatMap(URL.init) {
                        AsyncImage(url: imgURL) { img in
                            img.resizable().scaledToFit()
                        } placeholder: {
                            Color.gray.opacity(0.2)
                        }
                        .frame(maxHeight: 250)
                        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                        .padding(.horizontal)
                    }

                    SectionCard {
                        VStack(spacing: 14) {
                            LabeledField(label: "Title", text: $title)
                            Divider()
                            LabeledField(label: "Description", text: $description, axis: .vertical, minLines: 5)
                        }
                    }
                    .padding(.horizontal)

                    if !tags.isEmpty {
                        SectionCard {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Tags")
                                    .font(.caption.bold())
                                    .foregroundStyle(Theme.textSecondary)
                                    .textCase(.uppercase)
                                TagEditorView(tags: $tags)
                            }
                        }
                        .padding(.horizontal)
                    }

                    if let error { ErrorBanner(message: error).padding(.horizontal) }
                    if let successMessage { SuccessBanner(message: successMessage).padding(.horizontal) }

                    VStack(spacing: 10) {
                        ImproveMenu(isLoading: isImproving) { action in
                            Task { await improve(action: action) }
                        }
                        SecondaryButton("Translate") { showTranslate = true }
                    }
                    .padding(.horizontal)
                    .padding(.bottom)

                    Text("Improvements are previews — copy the text into Etsy to apply them.")
                        .font(.caption2)
                        .foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                        .padding(.bottom)
                }
                .padding(.vertical)
            }
        }
        .navigationTitle(listing.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                ShareLink(item: shareText) {
                    Image(systemName: "square.and.arrow.up")
                }
            }
        }
        .sheet(isPresented: $showTranslate) {
            TranslateSheet(title: title, description: description, tags: tags) { resp in
                title       = resp.title
                description = resp.description
                tags        = resp.tags
            }
        }
        .sheet(isPresented: $showUpgrade) { UpgradeView() }
    }

    private var shareText: String {
        "\(title)\n\n\(description)\n\n\(tags.map { "#\($0.replacingOccurrences(of: " ", with: ""))" }.joined(separator: " "))"
    }

    private var currentLang: String {
        let code = Locale.current.language.languageCode?.identifier ?? "en"
        return ["en", "de", "tr"].contains(code) ? code : "en"
    }

    private func improve(action: ImproveAction) async {
        isImproving = true
        error = nil
        do {
            let resp = try await APIClient.shared.improveListing(
                action: action, lang: currentLang,
                title: title, description: description, tags: tags
            )
            withAnimation {
                if let t = resp.title, !t.isEmpty       { title = t }
                if let d = resp.description, !d.isEmpty { description = d }
                if let tg = resp.tags, !tg.isEmpty      { tags = tg }
                successMessage = String(localized: "Listing improved.")
            }
            Haptics.success()
        } catch NetworkError.premiumRequired {
            showUpgrade = true
        } catch {
            self.error = error.localizedDescription
            Haptics.error()
        }
        isImproving = false
    }
}
