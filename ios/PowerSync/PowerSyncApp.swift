//
//  PowerSyncApp.swift
//  PowerSync
//
//  Native Swift 6 / SwiftUI entry point. Apple frameworks only.
//

import SwiftUI

@main
struct PowerSyncApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
        }
    }
}
