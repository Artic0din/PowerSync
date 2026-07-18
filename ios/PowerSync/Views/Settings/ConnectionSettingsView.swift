//
//  ConnectionSettingsView.swift
//  PowerSync
//

import SwiftUI

struct ConnectionSettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var url: String = ""
    @State private var token: String = ""
    @State private var showingToken = false

    var body: some View {
        Form {
            Section {
                TextField("https://homeassistant.local:8123", text: $url)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)

                if showingToken {
                    TextField("Long-lived access token", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } else {
                    SecureField("Long-lived access token", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Toggle("Show Token", isOn: $showingToken)
            } header: {
                Text("Home Assistant")
            } footer: {
                Text("Create a long-lived access token in your Home Assistant profile. PowerSync uses the native URLSession APIs only.")
            }

            if let error = model.connectionError {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }

            Section {
                Button {
                    Task {
                        await model.connect(url: url, token: token, demo: false)
                    }
                } label: {
                    if model.isBusy {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Save & Connect")
                            .frame(maxWidth: .infinity)
                    }
                }
                .disabled(url.isEmpty || token.isEmpty || model.isBusy)

                Button("Use Demo Mode") {
                    model.enterDemoMode()
                }
            }
        }
        .navigationTitle("Connection")
        .onAppear {
            url = model.homeAssistantURL
        }
    }
}
