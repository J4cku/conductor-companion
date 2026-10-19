import SwiftUI
import VisionKit

/// Pair with a Mac: scan its QR code, pick it from the network, or type its address and code.
struct PairingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var discovery = MacDiscovery()
    @State private var scanning = false
    @State private var address = ""
    @State private var code = ""
    @State private var working = false
    @State private var problem: String?
    var dismissable = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(spacing: 14) {
                        Image(systemName: "laptopcomputer.and.iphone")
                            .font(.system(size: 54)).symbolRenderingMode(.hierarchical).foregroundStyle(Color.accentColor)
                        Text("Pair with your Mac").font(.title2.bold())
                        Text("On the Mac, open the agents menu bar item and choose **Phone → Pair a phone…**")
                            .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        Button {
                            scanning = true
                        } label: {
                            Label("Scan the QR code", systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.glassProminent)
                        .controlSize(.large)
                        .disabled(!DataScannerViewController.isSupported)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                }
                .listRowBackground(Color.clear)

                if !discovery.found.isEmpty {
                    Section("Macs on this network") {
                        ForEach(discovery.found) { mac in
                            Button {
                                address = mac.base.absoluteString
                            } label: {
                                Label(mac.name, systemImage: address == mac.base.absoluteString ? "checkmark.circle.fill" : "laptopcomputer")
                            }
                        }
                    }
                }

                Section {
                    TextField("Address, e.g. 192.168.1.10:47823", text: $address)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("6-digit code", text: $code)
                        .keyboardType(.numberPad).textContentType(.oneTimeCode).font(.title3.monospacedDigit())
                    Button(working ? "Pairing…" : "Pair") { pairWithCode() }
                        .disabled(working || PairingLink(address) == nil || code.count != 6)
                } header: {
                    Text("Or type the code")
                } footer: {
                    if let problem { Text(problem).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Pair")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if dismissable { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            }
            .sheet(isPresented: $scanning) {
                QRScanner { text in
                    scanning = false
                    pair(scanned: text)
                }
                .ignoresSafeArea()
            }
            .onAppear { discovery.start() }
            .onDisappear { discovery.stop() }
        }
    }

    private func pair(scanned text: String) {
        guard let link = PairingLink(text), link.secret != nil else {
            problem = "That QR code isn't a pairing code from the Mac."
            return
        }
        run { try await model.pair(link, code: nil) }
    }

    private func pairWithCode() {
        guard let link = PairingLink(address) else { return }
        run { try await model.pair(PairingLink(base: link.base), code: code) }
    }

    private func run(_ work: @escaping () async throws -> Void) {
        working = true
        problem = nil
        Task {
            defer { working = false }
            do {
                try await work()
                dismiss()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

extension PairingLink {
    init(base: URL) { self.init(base.absoluteString)! }
}

/// The camera, looking for the pairing QR code.
struct QRScanner: UIViewControllerRepresentable {
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])], qualityLevel: .balanced,
                                                isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_ controller: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(found: found) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let found: (String) -> Void
        private var done = false

        init(found: @escaping (String) -> Void) { self.found = found }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for case let .barcode(code) in items {
                guard !done, let text = code.payloadStringValue else { continue }
                done = true
                scanner.stopScanning()
                found(text)
            }
        }
    }
}
