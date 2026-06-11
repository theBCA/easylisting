import SwiftUI

struct ResultView: View {
    @Environment(AppState.self) private var appState
    @State private var vm: ResultViewModel

    init(result: GenerateResponse) {
        _vm = State(initialValue: ResultViewModel(result: result))
    }

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 16) {
                    // Image strip (previews returned by the backend)
                    if !vm.result.imagePreviews.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 10) {
                                ForEach(Array(vm.result.imagePreviews.enumerated()), id: \.offset) { _, dataURL in
                                    DataURLImage(dataURL: dataURL)
                                        .frame(width: 120, height: 120)
                                        .clipShape(RoundedRectangle(cornerRadius: 10))
                                }
                            }
                            .padding(.horizontal)
                        }
                    }

                    // Category path
                    if let path = vm.result.taxonomyPath, !path.isEmpty {
                        SectionCard {
                            HStack(spacing: 8) {
                                Image(systemName: "folder")
                                    .foregroundStyle(Theme.purple)
                                Text(path)
                                    .font(.caption)
                                    .foregroundStyle(Theme.textSecondary)
                                Spacer()
                            }
                        }
                        .padding(.horizontal)
                    }

                    SectionCard {
                        VStack(spacing: 16) {
                            LabeledField(label: "Title", text: $vm.title)
                            Divider()
                            LabeledField(label: "Description", text: $vm.description, axis: .vertical, minLines: 6)
                        }
                    }
                    .padding(.horizontal)

                    SectionCard {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Tags")
                                .font(.caption.bold())
                                .foregroundStyle(Theme.textSecondary)
                                .textCase(.uppercase)
                            TagEditorView(tags: $vm.tags)
                        }
                    }
                    .padding(.horizontal)

                    SectionCard {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Price")
                                .font(.caption.bold())
                                .foregroundStyle(Theme.textSecondary)
                                .textCase(.uppercase)
                            TextField("0.00", value: $vm.price, format: .number)
                                .keyboardType(.decimalPad)
                        }
                    }
                    .padding(.horizontal)

                    if !vm.result.shippingProfiles.isEmpty {
                        SectionCard {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Shipping Profile")
                                    .font(.caption.bold())
                                    .foregroundStyle(Theme.textSecondary)
                                    .textCase(.uppercase)
                                Picker("Shipping", selection: $vm.shippingProfileId) {
                                    Text("Select").tag(Optional<Int>.none)
                                    ForEach(vm.result.shippingProfiles) { profile in
                                        Text(profile.title).tag(Optional(profile.id))
                                    }
                                }
                                .pickerStyle(.menu)
                            }
                        }
                        .padding(.horizontal)
                    }

                    if let error = vm.error {
                        ErrorBanner(message: error).padding(.horizontal)
                    }
                    if let success = vm.successMessage {
                        SuccessBanner(message: success).padding(.horizontal)
                    }

                    VStack(spacing: 10) {
                        // Publish requires an Etsy-connected account and a resolved category
                        if !appState.isGuest {
                            PrimaryButton("Publish to Etsy", isLoading: vm.isPublishing) {
                                Task { await vm.publish() }
                            }
                            .disabled(!vm.canPublish || vm.publishedListingId != nil)
                            .opacity(vm.canPublish && vm.publishedListingId == nil ? 1 : 0.5)
                        }

                        HStack(spacing: 10) {
                            ImproveMenu(isLoading: vm.isImproving) { action in
                                Task { await vm.improve(action: action, lang: currentLang) }
                            }
                            SecondaryButton("Translate") {
                                vm.showTranslate = true
                            }
                        }
                    }
                    .padding(.horizontal)
                    .padding(.bottom)
                }
                .padding(.vertical)
            }
        }
        .navigationTitle("Generated Listing")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $vm.showTranslate) {
            TranslateSheet(title: vm.title, description: vm.description, tags: vm.tags) { translated in
                vm.applyTranslation(translated)
            }
        }
        .sheet(isPresented: $vm.needsUpgrade) {
            UpgradeView()
        }
    }

    private var currentLang: String {
        let code = Locale.current.language.languageCode?.identifier ?? "en"
        return ["en", "de", "tr"].contains(code) ? code : "en"
    }
}

// MARK: - Improve menu (maps to backend actions)

struct ImproveMenu: View {
    let isLoading: Bool
    let onSelect: (ImproveAction) -> Void

    var body: some View {
        Menu {
            Button { onSelect(.titleSEO) } label: {
                Label("SEO Title", systemImage: "magnifyingglass")
            }
            Button { onSelect(.descriptionWarm) } label: {
                Label("Warmer Description", systemImage: "heart")
            }
            Button { onSelect(.tags) } label: {
                Label("Regenerate Tags", systemImage: "tag")
            }
            Button { onSelect(.shorten) } label: {
                Label("Shorten", systemImage: "arrow.down.right.and.arrow.up.left")
            }
        } label: {
            HStack(spacing: 8) {
                if isLoading { ProgressView().tint(Theme.purple) }
                Text("Improve")
                    .font(.system(size: 16, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Theme.purpleLight)
            .foregroundStyle(Theme.purple)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(isLoading)
    }
}

// MARK: - Base64 data-URL image

struct DataURLImage: View {
    let dataURL: String

    var body: some View {
        if let image = decoded {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.gray.opacity(0.15))
                .overlay { Image(systemName: "photo").foregroundStyle(Theme.textSecondary) }
        }
    }

    private var decoded: UIImage? {
        guard let comma = dataURL.firstIndex(of: ","),
              let data = Data(base64Encoded: String(dataURL[dataURL.index(after: comma)...])) else { return nil }
        return UIImage(data: data)
    }
}

// MARK: - Tag editor

struct TagEditorView: View {
    @Binding var tags: [String]
    @State private var newTag = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FlowLayout(spacing: 6) {
                ForEach(tags, id: \.self) { tag in
                    HStack(spacing: 4) {
                        Text(tag).font(.caption)
                        Button {
                            withAnimation(.spring(duration: 0.25)) {
                                tags.removeAll { $0 == tag }
                            }
                        } label: {
                            Image(systemName: "xmark").font(.system(size: 8))
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Theme.purpleLight)
                    .foregroundStyle(Theme.purple)
                    .clipShape(Capsule())
                }
            }

            if tags.count < 13 {
                HStack {
                    TextField("Add tag…", text: $newTag)
                        .font(.caption)
                        .onSubmit { addTag() }
                    Button("Add") { addTag() }
                        .font(.caption)
                        .foregroundStyle(Theme.purple)
                }
            }
        }
    }

    private func addTag() {
        let t = newTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, t.count <= 20, !tags.contains(t), tags.count < 13 else { return }
        withAnimation(.spring(duration: 0.25)) { tags.append(t) }
        newTag = ""
    }
}

// MARK: - Flow layout

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0, maxY: CGFloat = 0
        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 {
                y += lineH + spacing
                x = 0; lineH = 0
            }
            x += size.width + spacing
            lineH = max(lineH, size.height)
            maxY  = max(maxY, y + lineH)
        }
        return CGSize(width: width, height: maxY)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineH: CGFloat = 0
        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                y += lineH + spacing
                x = bounds.minX; lineH = 0
            }
            sv.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            lineH = max(lineH, size.height)
        }
    }
}

// MARK: - Translate sheet (backend supports en / de / tr)

struct TranslateSheet: View {
    let title: String
    let description: String
    let tags: [String]
    let onApply: (TranslateResponse) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var targetLang = "en"
    @State private var isTranslating = false
    @State private var error: String?

    private let langs = [("en", "English"), ("de", "Deutsch"), ("tr", "Türkçe")]

    var body: some View {
        NavigationStack {
            Form {
                Section("Target Language") {
                    Picker("Language", selection: $targetLang) {
                        ForEach(langs, id: \.0) { code, name in
                            Text(verbatim: name).tag(code)
                        }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }
                if let error { Section { ErrorBanner(message: error) } }
            }
            .navigationTitle("Translate")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if isTranslating {
                        ProgressView()
                    } else {
                        Button("Translate") { Task { await translate() } }
                    }
                }
            }
        }
    }

    private func translate() async {
        isTranslating = true
        error = nil
        do {
            let resp = try await APIClient.shared.translate(
                title: title, description: description, tags: tags, lang: targetLang
            )
            onApply(resp)
            Haptics.success()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
        isTranslating = false
    }
}
