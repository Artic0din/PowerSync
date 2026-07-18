//
//  OnboardingView.swift
//  PowerSync
//

import SwiftUI

struct OnboardingView: View {
    @Environment(AppModel.self) private var model
    @State private var showingConnection = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 28) {
                    Spacer(minLength: 24)

                    Image(systemName: "bolt.circle.fill")
                        .font(.system(size: 88))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.mint)
                        .accessibilityHidden(true)

                    VStack(spacing: 8) {
                        Text("Welcome to PowerSync")
                            .font(.largeTitle.weight(.bold))
                            .multilineTextAlignment(.center)
                        Text("Intelligent battery energy management")
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }

                    VStack(alignment: .leading, spacing: 14) {
                        featureRow("sun.max.fill", "Optimize with live prices and solar", .orange)
                        featureRow("battery.100.bolt", "Automate charge, discharge, and export", .cyan)
                        featureRow("dollarsign.circle.fill", "Cut your power bill and track savings", .green)
                    }
                    .padding()
                    .glassEffect(.regular, in: .rect(cornerRadius: 24))

                    VStack(spacing: 12) {
                        Button {
                            showingConnection = true
                        } label: {
                            Label("Connect Home Assistant", systemImage: "house.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)

                        Button("Try Demo Mode") {
                            model.enterDemoMode()
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }

                    Text("By continuing you agree to the Terms and Privacy Policy.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)

                    Spacer(minLength: 24)
                }
                .padding(.horizontal, 24)
            }
            .background(Color(.systemBackground))
            .navigationDestination(isPresented: $showingConnection) {
                ConnectionSettingsView()
            }
        }
    }

    private func featureRow(_ image: String, _ text: String, _ tint: Color) -> some View {
        Label {
            Text(text)
                .font(.subheadline)
        } icon: {
            Image(systemName: image)
                .foregroundStyle(tint)
        }
    }
}
