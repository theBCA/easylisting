import SwiftUI
import PhotosUI

struct GenerateView: View {
    @Environment(AppState.self) private var appState
    @State private var vm = GenerateViewModel()
    @State private var showResult = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()

            ScrollView {
                VStack(spacing: 20) {
                    // Header
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Hello\(appState.shopName.map { ", \($0)" } ?? "")!")
                                .font(.title2.bold())
                            if appState.isPremium {
                                Label("Premium", systemImage: "crown.fill")
                                    .font(.caption)
                                    .foregroundStyle(.orange)
                            } else {
                                Text("\(appState.remaining) free generations left")
                                    .font(.caption)
                                    .foregroundStyle(Theme.textSecondary)
                            }
                        }
                        Spacer()
                    }
                    .padding(.horizontal)

                    PhotoPickerSection(selectedImages: $vm.selectedImages)

                    SectionCard {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Product Hint (Optional)")
                                .font(.caption.bold())
                                .foregroundStyle(Theme.textSecondary)
                                .textCase(.uppercase)
                            TextField("E.g. handmade ceramic mug, vintage style...", text: $vm.hint, axis: .vertical)
                                .lineLimit(3, reservesSpace: false)
                        }
                    }
                    .padding(.horizontal)

                    SectionCard {
                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Language").font(.caption.bold()).foregroundStyle(Theme.textSecondary).textCase(.uppercase)
                                Picker("Language", selection: $vm.lang) {
                                    Text(verbatim: "English").tag("en")
                                    Text(verbatim: "Türkçe").tag("tr")
                                    Text(verbatim: "Deutsch").tag("de")
                                }
                                .pickerStyle(.menu)
                            }
                            Divider()
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Platform").font(.caption.bold()).foregroundStyle(Theme.textSecondary).textCase(.uppercase)
                                Picker("Platform", selection: $vm.platform) {
                                    Text(verbatim: "Etsy").tag("etsy")
                                    Text(verbatim: "Trendyol").tag("trendyol")
                                    Text(verbatim: "Shopify").tag("shopify")
                                    Text(verbatim: "Amazon").tag("amazon")
                                }
                                .pickerStyle(.menu)
                            }
                        }
                    }
                    .padding(.horizontal)

                    if let error = vm.error {
                        ErrorBanner(message: error).padding(.horizontal)
                    }

                    PrimaryButton(
                        vm.isGenerating ? "Generating…" : "Create Listing",
                        isLoading: vm.isGenerating
                    ) {
                        Task { await generate() }
                    }
                    .padding(.horizontal)
                    .disabled(vm.selectedImages.isEmpty)
                    .opacity(vm.selectedImages.isEmpty ? 0.5 : 1)
                }
                .padding(.vertical)
            }
        }
        .navigationTitle("Create Listing")
        .navigationBarTitleDisplayMode(.large)
        .navigationDestination(isPresented: $showResult) {
            if let result = vm.result {
                ResultView(result: result)
            }
        }
        .sheet(isPresented: $vm.needsEmailVerification) {
            MagicLinkView()
        }
        .sheet(isPresented: $vm.limitReached) {
            UpgradeView()
        }
    }

    private func generate() async {
        await vm.generate()
        if vm.result != nil {
            showResult = true
            await appState.refresh()
        }
    }
}

// MARK: - Photo picker section

struct PhotoPickerSection: View {
    @Binding var selectedImages: [UIImage]
    @State private var pickerItems: [PhotosPickerItem] = []
    @State private var showCamera = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Photos")
                    .font(.caption.bold())
                    .foregroundStyle(Theme.textSecondary)
                    .textCase(.uppercase)
                Spacer()
                Text(verbatim: "\(selectedImages.count)/5")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    if selectedImages.count < 5 {
                        Menu {
                            PhotosPicker(selection: $pickerItems,
                                         maxSelectionCount: 5 - selectedImages.count,
                                         matching: .images) {
                                Label("Photo Library", systemImage: "photo.on.rectangle")
                            }
                            Button {
                                showCamera = true
                            } label: {
                                Label("Camera", systemImage: "camera")
                            }
                        } label: {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(Theme.purple, style: StrokeStyle(lineWidth: 2, dash: [6]))
                                .frame(width: 100, height: 100)
                                .overlay {
                                    VStack(spacing: 4) {
                                        Image(systemName: "plus").font(.title2)
                                        Text("Add").font(.caption)
                                    }
                                    .foregroundStyle(Theme.purple)
                                }
                        }
                    }

                    ForEach(Array(selectedImages.enumerated()), id: \.offset) { i, img in
                        ZStack(alignment: .topTrailing) {
                            Image(uiImage: img)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 100, height: 100)
                                .clipShape(RoundedRectangle(cornerRadius: 12))

                            Button {
                                withAnimation(.spring(duration: 0.25)) {
                                    _ = selectedImages.remove(at: i)
                                }
                                pickerItems = []
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.white, .black.opacity(0.6))
                                    .font(.title3)
                            }
                            .padding(4)
                        }
                    }
                }
                .padding(.horizontal)
            }
        }
        .onChange(of: pickerItems) { _, items in
            guard !items.isEmpty else { return }
            Task {
                for item in items {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let img  = UIImage(data: data),
                       selectedImages.count < 5 {
                        withAnimation(.spring(duration: 0.25)) {
                            selectedImages.append(img)
                        }
                    }
                }
                pickerItems = []
            }
        }
        .fullScreenCover(isPresented: $showCamera) {
            CameraView { img in
                withAnimation(.spring(duration: 0.25)) {
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
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraView
        init(_ parent: CameraView) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let img = info[.originalImage] as? UIImage {
                parent.onCapture(img)
            }
            parent.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}
