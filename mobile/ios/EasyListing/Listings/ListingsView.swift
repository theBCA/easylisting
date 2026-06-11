import SwiftUI

struct ListingsView: View {
    @State private var listings: [EtsyListing] = []
    @State private var state: String = "active"
    @State private var isLoading = false
    @State private var error: String?

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()

            VStack(spacing: 0) {
                Picker("Status", selection: $state) {
                    Text("Active").tag("active")
                    Text("Draft").tag("draft")
                }
                .pickerStyle(.segmented)
                .padding()

                if isLoading {
                    ProgressView().padding(.top, 40)
                    Spacer()
                } else if let error {
                    VStack(spacing: 12) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.largeTitle)
                            .foregroundStyle(Theme.textSecondary)
                        Text(error).font(.subheadline).foregroundStyle(Theme.textSecondary)
                        Button("Try Again") { Task { await load() } }
                            .buttonStyle(.bordered)
                    }
                    .padding(.top, 40)
                    Spacer()
                } else if listings.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "tag.slash").font(.largeTitle).foregroundStyle(Theme.textSecondary)
                        Text("No listings yet").font(.headline)
                        Text("Add a new listing from the \"Create\" tab.")
                            .font(.subheadline).foregroundStyle(Theme.textSecondary)
                    }
                    .padding(.top, 40)
                    Spacer()
                } else {
                    List(listings) { listing in
                        NavigationLink(destination: ListingDetailView(listing: listing)) {
                            ListingRow(listing: listing)
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
        }
        .navigationTitle("My Listings")
        .navigationBarTitleDisplayMode(.large)
        .onChange(of: state) { _, _ in Task { await load() } }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        isLoading = true
        error = nil
        do {
            listings = try await APIClient.shared.listings(state: state)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct ListingRow: View {
    let listing: EtsyListing

    var body: some View {
        HStack(spacing: 12) {
            if let imgURL = listing.images?.first?.url570xN.flatMap(URL.init) {
                AsyncImage(url: imgURL) { img in
                    img.resizable().scaledToFill()
                } placeholder: {
                    Color.gray.opacity(0.2)
                }
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.gray.opacity(0.2))
                    .frame(width: 56, height: 56)
                    .overlay { Image(systemName: "photo").foregroundStyle(Theme.textSecondary) }
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(listing.title)
                    .font(.subheadline.bold())
                    .lineLimit(2)
                if let price = listing.price {
                    Text(price.display)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
