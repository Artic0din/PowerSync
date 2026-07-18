//
//  AutomationsView.swift
//  PowerSync
//

import SwiftUI

struct AutomationsView: View {
    @Environment(AppModel.self) private var model

    private var activeCount: Int {
        model.automations.filter(\.isEnabled).count
    }

    var body: some View {
        List {
            Section {
                ForEach(model.automations) { rule in
                    Toggle(isOn: binding(for: rule)) {
                        Label {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(rule.title)
                                    .font(.headline)
                                Text("\(rule.triggerSummary) → \(rule.actionSummary)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: rule.systemImage)
                                .foregroundStyle(.white)
                                .frame(width: 32, height: 32)
                                .background(.blue.gradient, in: .rect(cornerRadius: 8))
                        }
                    }
                }
            } footer: {
                Text("\(model.automations.count) automations · \(activeCount) active")
            }
        }
        .navigationTitle("Automations")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    // Placeholder for future rule editor — keeps HIG toolbar pattern.
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .disabled(true)
            }
        }
    }

    private func binding(for rule: AutomationRule) -> Binding<Bool> {
        Binding(
            get: {
                model.automations.first(where: { $0.id == rule.id })?.isEnabled ?? false
            },
            set: { model.setAutomation(rule, enabled: $0) }
        )
    }
}
