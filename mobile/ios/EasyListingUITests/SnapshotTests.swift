import XCTest

@MainActor
final class SnapshotTests: XCTestCase {

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    // MARK: - Helpers

    private func launch(screen: String) -> XCUIApplication {
        let app = XCUIApplication()
        setupSnapshot(app)
        app.launchEnvironment["SNAPSHOT_SCREEN"] = screen
        app.launch()
        return app
    }

    // MARK: - Screenshot tests

    func test01_auth() {
        let app = XCUIApplication()
        setupSnapshot(app)
        app.launchEnvironment["SNAPSHOT_SCREEN"] = "auth"
        app.launch()
        sleep(1)
        snapshot("01-auth")
    }

    func test02_generate() {
        let app = launch(screen: "generate")
        // Wait for the thumbnail to appear (image is pre-loaded)
        sleep(1)
        snapshot("02-generate")
    }

    func test03_result() {
        let app = launch(screen: "result")
        // Wait for navigation push to settle
        sleep(2)
        snapshot("03-result")
    }

    func test04_listings() {
        let app = launch(screen: "listings")
        sleep(1)
        snapshot("04-listings")
    }

    func test05_settings() {
        let app = launch(screen: "settings")
        sleep(1)
        snapshot("05-settings")
    }
}
