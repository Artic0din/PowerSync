//
//  Formatters.swift
//  PowerSync
//

import Foundation

enum Formatters {
    static let currency: FloatingPointFormatStyle<Double>.Currency = .currency(code: "AUD")
        .precision(.fractionLength(2))

    static let percent: FloatingPointFormatStyle<Double> = .number.precision(.fractionLength(0))
    static let oneDecimal: FloatingPointFormatStyle<Double> = .number.precision(.fractionLength(1))
    static let cents: FloatingPointFormatStyle<Double> = .number.precision(.fractionLength(1))

    static let time: Date.FormatStyle = .dateTime.hour().minute()
    static let timeWithDay: Date.FormatStyle = .dateTime.weekday(.abbreviated).hour().minute()
    static let month: Date.FormatStyle = .dateTime.month(.abbreviated)

    static func money(_ value: Double) -> String {
        value.formatted(currency)
    }

    static func signedMoney(_ value: Double) -> String {
        let formatted = abs(value).formatted(currency)
        if value > 0 { return "+\(formatted)" }
        if value < 0 { return "-\(formatted)" }
        return formatted
    }

    static func kw(_ value: Double) -> String {
        "\(value.formatted(oneDecimal)) kW"
    }

    static func kwh(_ value: Double) -> String {
        if abs(value) < 1 {
            return "\((value * 1000).formatted(.number.precision(.fractionLength(0)))) Wh"
        }
        return "\(value.formatted(oneDecimal)) kWh"
    }

    static func centsPerKWh(_ value: Double) -> String {
        "\(value.formatted(cents))¢/kWh"
    }
}
