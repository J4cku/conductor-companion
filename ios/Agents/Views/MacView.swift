import SwiftUI
import UserNotifications

/// The paired Macs, away mode and the connection.
struct MacView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openURL) private var openURL
    @State private var pairing = false
    @State private var notifications: UNAuthorizationStatus = .notDetermined

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
                        LabeledContent("Updates") {
                            Text(model.live ? "Live" : "Every few seconds").foregroundStyle(.secondary)
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
                Section {
                    Toggle(isOn: Binding(get: { notifications == .authorized }, set: { on in
                        if on { Task { await model.enableNotifications(); await readNotificationSettings() } } else { openSettings() }
                    })) {
                        Label("Notifications", systemImage: "bell.badge.fill")
                    }
                    @Bindable var model = model
                    Toggle(isOn: $model.requireFaceID) {
                        Label("Require Face ID", systemImage: "faceid")
                    }
                } header: {
                    Text("This iPhone")
                } footer: {
                    Text("Notifications arrive when an agent finishes or asks something; reply right from the lock screen. They need the Mac set up for push (see the README).")
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
            .scrollContentBackground(.hidden)
            .background { AmbientBackground(tint: model.accent, intensity: 0.5) }
            .task { await readNotificationSettings() }
        }
    }

    private func readNotificationSettings() async {
        notifications = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
    }

    private func openSettings() {
        if let url = URL(string: UIApplication.openNotificationSettingsURLString) { openURL(url) }
    }
}
