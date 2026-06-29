export default function Results({ score, questions, sessionType, onBack }) {
    return (
        <div className="session-view">
            <h1>Session Complete!</h1>
            <h2>Score: {score} / {questions.length}</h2>
            <p>{sessionType === "unit_test" ? "Unit test results submitted." : "Practice session results saved."}</p>
            <button onClick={onBack}>Back</button>
        </div>
    );
}