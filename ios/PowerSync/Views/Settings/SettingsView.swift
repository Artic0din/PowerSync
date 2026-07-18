//
//  SettingsView.swift
//  PowerSync
//

import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model

        List {
            Section {
                HStack {
                    Label(model.batterySystemName, systemImage: "battery.100.bolt")
                    Spacer()
                    if model.isBatteryAutoDetected {
                        Text("Auto-detected")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.green)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(.green.opacity(0.15), in: .capsule)
                    }
                }
            } header: {
                Text("Battery System")
            }

            Section("Integrated Services") {
                NavigationLink {
                    BatteryHealthView()
                } label: {
                    Label {
                        VStack(alignment: .leading) {
                            Text("Battery Health")
                            Text("Scan and monitor capacity")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "heart.text.clipboard")
                    }
                }

                NavigationLink {
                    EVChargingView()
                } label: {
                    Label {
                        VStack(alignment: .leading) {
                            Text("EV Charging")
                            Text("Vehicles and chargers")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "car.fill")
                    }
                }

                NavigationLink {
                    PricesView()
                } label: {
                    Label {
                        VStack(alignment: .leading) {
                            Text("Electricity Provider")
                            Text("Amber, Flow Power, GloBird")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "bolt.fill")
                    }
                }

                Label {
                    VStack(alignment: .leading) {
                        Text("Weather & Solar Forecast")
                        Text("Solcast, Open-Meteo")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } icon: {
                    Image(systemName: "sun.max.fill")
                }
            }

            Section("Connection") {
                if model.usesDemoMode {
                    LabeledContent("Mode", value: "Demo")
                } else {
                    LabeledContent("Home Assistant", value: model.homeAssistantURL)
                }

                Toggle("Demo Mode", systemImage: "flask.fill", isOn: Binding(
                    get: { model.usesDemoMode },
                    set: { enabled in
                        if enabled {
                            Task { await model.disconnect() }
                        }
                    }
                ))

                NavigationLink("Connect Home Assistant") {
                    ConnectionSettingsView()
                }
            }

            Section("About") {
                LabeledContent("PowerSync", value: "2.12")
                Link("Documentation", destination: URL(string: "https://github.com/bolagnaise/PowerSync")!)
                Link("Privacy Policy", destination: URL(string: "https://bolagnaise.github.io/PowerSync/privacy.html")!)
            }
        }
        .navigationTitle("Settings")
    }
}
