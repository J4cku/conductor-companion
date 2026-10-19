import SwiftUI

/// The paired Macs, away mode and the connection.
struct MacView: View {
    @Environment(AppModel.self) private var model
    @State private var pairing = false

    var body: some View {
        NavigationStack {
            Form {
                if let mac = model.mac {
                    Section {
                        LabeledContent("Computer", value: model.snapshot?.computer ?? mac.name)
                        LabeledContent("Address", value: mac.base.host() ?? mac.base.absoluteString)
                        LabeledContent("Status") {
                            Text(model.problem == nil ? "Connected" : "Offline").foregroundStyle(model.problem == nil ? .green : .red)
                        }
                        if let problem = model.problem {
                            Text(problem).font(.footnote).foregroundStyle(.secondary)
                        }
                        if let theme = model.snapshot?.theme { LabeledContent("Theme", value: theme.name) }
                    }
                    Section {
                        Toggle(isOn: Binding(get: { model.snapshot?.away ?? false }, set: { on in Task { await model.setAway(on) } })) {
                            Label("Away mode", systemImage: "figure.walk.departure")
                        }
                    } footer: {
                        Text("While away mode is on, an agent's question waits here for your answer instead of on the Mac. Anything you type in Conductor still wins.")
                    }
                }
                Section("Paired Macs") {
                    ForEach(model.macs) { mac in
                        Button {
                            model.select(mac)
                        } label: {
                            HStack {
                                Label(mac.name, systemImage: "laptopcomputer")
                                Spacer()
                                if mac.id == model.mac?.id { Image(systemName: "checkmark").foregroundStyle(Color.accentColor) }
                            }
                        }
                        .tint(.primary)
                        .swipeActions { Button("Forget", role: .destructive) { model.forget(mac) } }
                    }
                    Button("Pair another Mac", systemImage: "plus") { pairing = true }
                }
            }
            .navigationTitle("Mac")
            .sheet(isPresented: $pairing) { PairingView(dismissable: true) }
        }
    }
}
