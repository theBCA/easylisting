import SwiftUI
import PhotosUI

struct GenerateView: View {
    var snapshotResult: GenerateResponse? = nil

    @Environment(AppState.self) private var appState
    @State private var vm = GenerateViewModel()
    @State private var showResult = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: Theme.Space.md) {
                    // ── Header ────────────────────────────────────────
                    HStack(alignment: .center) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(appState.shopName.map { "Hello, \($0) 👋" } ?? "Create a Listing")
                                .font(.title3.bold())
                            if appState.isPremium {
                                PremiumBadge()
                            } else {
                                RemainingPill(count: appState.remaining)
                            }
                        }
                        Spacer()
                        // App icon mark
                        ZStack {
                            Circle()
                                .fill(Theme.purpleGradient)
                                .frame(width: 42, height: 42)
                            Image(systemName: "tag.fill")
                                .font(.system(size: 17))
                                .foregroundStyle(.white)
                        }
                    }
                    .padding(.horizontal, Theme.Space.md)
                    .padding(.top, 4)

                    // ── Photo picker ──────────────────────────────────
                    PhotoPickerSection(selectedImages: $vm.selectedImages)

                    // ── Product description ───────────────────────────
                    SectionCard(title: "Product Description") {
                        TextField(
                            "E.g. handmade ceramic mug, vintage-style glaze, 350 ml…",
                            text: $vm.hint,
                            axis: .vertical
                        )
                        .lineLimit(3, reservesSpace: false)
                        .font(.body)
                    }
                    .padding(.horizontal, Theme.Space.md)

                    // ── Language & Platform ───────────────────────────
                    HStack(spacing: 10) {
                        PickerCard(label: "Language", systemImage: "globe") {
                            Picker("Language", selection: $vm.lang) {
                                Text(verbatim: "English").tag("en")
                                Text(verbatim: "Türkçe").tag("tr")
                                Text(verbatim: "Deutsch").tag("de")
                            }
                            .pickerStyle(.menu)
                            .tint(Theme.purple)
                        }

                        PickerCard(label: "Platform", systemImage: "storefront") {
                            Picker("Platform", selection: $vm.platform) {
                                Text(verbatim: "Etsy").tag("etsy")
                                Text(verbatim: "Trendyol").tag("trendyol")
                                Text(verbatim: "Shopify").tag("shopify")
                                Text(verbatim: "Amazon").tag("amazon")
                            }
                            .pickerStyle(.menu)
                            .tint(Theme.purple)
                        }
                    }
                    .padding(.horizontal, Theme.Space.md)

                    // ── Error ─────────────────────────────────────────
                    if let error = vm.error {
                        ErrorBanner(message: error)
                            .padding(.horizontal, Theme.Space.md)
                            .transition(.move(edge: .top).combined(with: .opacity))
                    }

                    // ── CTA ───────────────────────────────────────────
                    VStack(spacing: 8) {
                        PrimaryButton(vm.isGenerating ? "Generating…" : "Create Listing",
                                      isLoading: vm.isGenerating) {
                            Task { await generate() }
                        }
                        .disabled(vm.selectedImages.isEmpty)
                        .opacity(vm.selectedImages.isEmpty ? 0.45 : 1)

                        if vm.selectedImages.isEmpty {
                            Text("Add at least one photo to continue")
                                .font(.caption)
                                .foregroundStyle(Theme.textTertiary)
                        }
                    }
                    .padding(.horizontal, Theme.Space.md)
                    .padding(.bottom, Theme.Space.xl)
                    .animation(.easeInOut(duration: 0.2), value: vm.selectedImages.isEmpty)
                }
                .padding(.top, Theme.Space.sm)
                .animation(.easeInOut(duration: 0.2), value: vm.error)
            }
        }
        .navigationTitle("Create")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(isPresented: $showResult) {
            if let result = vm.result { ResultView(result: result) }
        }
        .sheet(isPresented: $vm.needsEmailVerification) { MagicLinkView() }
        .sheet(isPresented: $vm.limitReached) { UpgradeView() }
        #if DEBUG
        .onAppear { applySnapshotState() }
        #endif
    }

    #if DEBUG
    private func applySnapshotState() {
        guard SnapshotData.isActive else { return }
        if let result = snapshotResult {
            vm.result = result
            vm.selectedImages = [SnapshotData.sampleProductImage]
            vm.hint = "Handmade ceramic mug, vintage-style glaze, 350ml"
            showResult = true
        } else if SnapshotData.screen == "generate" {
            vm.selectedImages = [SnapshotData.sampleProductImage]
            vm.hint = "Handmade ceramic mug, vintage-style glaze, 350ml"
        }
    }
    #endif

    private func generate() async {
        await vm.generate()
        if vm.result != nil {
            showResult = true
            await appState.refresh()
        }
    }
}

// MARK: - Picker card

private struct PickerCard<Content: View>: View {
    let label: String
    let systemImage: String
    let content: Content

    init(label: String, systemImage: String, @ViewBuilder content: () -> Content) {
        self.label = label; self.systemImage = systemImage; self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Image(systemName: systemImage)
                    .font(.caption.weight(.semibold))
                Text(label)
                    .font(.footnote.weight(.semibold))
            }
            .foregroundStyle(Theme.textSecondary)
            content
        }
        .padding(.horizontal, Theme.Space.md)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
        .shadow(color: Theme.cardShadow, radius: Theme.cardShadowRadius, y: 3)
    }
}

// MARK: - Photo picker section

struct PhotoPickerSection: View {
    @Binding var selectedImages: [UIImage]
    @State private var pickerItems: [PhotosPickerItem] = []
    @State private var showCamera = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Product Photos")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                Spacer()
                Text("\(selectedImages.count)/5")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(selectedImages.isEmpty ? Theme.textTertiary : Theme.purple)
            }
            .padding(.horizontal, Theme.Space.md)

            if selectedImages.isEmpty {
                // ── Empty drop-zone ───────────────────────────────
                PhotosPicker(
                    selection: $pickerItems,
                    maxSelectionCount: 5,
                    matching: .images
                ) {
                    VStack(spacing: 12) {
                        ZStack {
                            Circle()
                                .fill(Theme.purpleLight)
                                .frame(width: 64, height: 64)
                            Image(systemName: "camera.fill")
                                .font(.system(size: 26))
                                .foregroundStyle(Theme.purple)
                        }
                        VStack(spacing: 4) {
                            Text("Add Product Photos")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Theme.textPrimary)
                            Text("Tap to choose from library • up to 5")
                                .font(.caption)
                                .foregroundStyle(Theme.textSecondary)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 168)
                    .background(Theme.card)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.radius)
                            .strokeBorder(
                                Theme.purple.opacity(0.35),
                                style: StrokeStyle(lineWidth: 1.5, dash: [6, 4])
                            )
                    )
                    .shadow(color: Theme.cardShadow, radius: 8, y: 2)
                }
                .padding(.horizontal, Theme.Space.md)
                .buttonStyle(.plain)
            } else {
                // ── Thumbnail strip ───────────────────────────────
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(Array(selectedImages.enumerated()), id: \.offset) { i, img in
                            ZStack(alignment: .topTrailing) {
                                Image(uiImage: img)
                                    .resizable()
                                    .scaledToFill()
                                    .frame(width: 110, height: 110)
                                    .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: Theme.radiusSmall)
                                            .stroke(Theme.border, lineWidth: 1)
                                    )

                                Button {
                                    withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                                        selectedImages.remove(at: i)
                                    }
                                    pickerItems = []
                                } label: {
                                    ZStack {
                                        Circle().fill(.black.opacity(0.55)).frame(width: 22, height: 22)
                                        Image(systemName: "xmark")
                                            .font(.system(size: 9, weight: .bold))
                                            .foregroundStyle(.white)
                                    }
                                }
                                .offset(x: 4, y: -4)
                            }
                        }

                        if selectedImages.count < 5 {
                            Menu {
                                PhotosPicker(
                                    selection: $pickerItems,
                                    maxSelectionCount: 5 - selectedImages.count,
                                    matching: .images
                                ) {
                                    Label("Photo Library", systemImage: "photo.on.rectangle")
                                }
                                Button { showCamera = true } label: {
                                    Label("Camera", systemImage: "camera")
                                }
                            } label: {
                                VStack(spacing: 6) {
                                    Image(systemName: "plus")
                                        .font(.title3.weight(.semibold))
                                        .foregroundStyle(Theme.purple)
                                    Text("Add")
                                        .font(.caption.weight(.medium))
                                        .foregroundStyle(Theme.purple)
                                }
                                .frame(width: 110, height: 110)
                                .background(Theme.purpleLight)
                                .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall))
                                .overlay(
                                    RoundedRectangle(cornerRadius: Theme.radiusSmall)
                                        .stroke(Theme.purple.opacity(0.3), lineWidth: 1)
                                )
                            }
                        }
                    }
                    .padding(.horizontal, Theme.Space.md)
                    .padding(.vertical, 2)
                }
            }
        }
        .onChange(of: pickerItems) { _, items in
            guard !items.isEmpty else { return }
            Task {
                for item in items {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let img = UIImage(data: data),
                       selectedImages.count < 5 {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                            selectedImages.append(img)
                        }
                    }
                }
                pickerItems = []
            }
        }
        .fullScreenCover(isPresented: $showCamera) {
            CameraView { img in
                withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                    selectedImages.append(img)
                }
            }
            .ignoresSafeArea()
        }
    }
}

// MARK: - Camera wrapper

struct CameraView: UIViewControllerRepresentable {
    let onCapture: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = .camera
        p.delegate = context.coordinator
        return p
    }
    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraView
        init(_ parent: CameraView) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let img = info[.originalImage] as? UIImage { parent.onCapture(img) }
            parent.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}
