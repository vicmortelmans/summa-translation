import argparse
import base64
import json
import os

from dotenv import load_dotenv
from mistralai.client import Mistral
from mistralai.client.models import ResponseFormat, JSONSchema

script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, "api_key"))  # contains MISTRAL_API_KEY


def encode_file(file_path):
    with open(file_path, "rb") as pdf_file:
        return base64.b64encode(pdf_file.read()).decode('utf-8')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Mistral OCR on a PDF and save the full response as JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python ocr-axters-mistral.py axters-scholastiek-lexicon-1937_text.pdf
  python ocr-axters-mistral.py part1.pdf part1.json
        """,
    )
    parser.add_argument(
        "input_pdf",
        help="Path to the input PDF file",
    )
    parser.add_argument(
        "output_json",
        nargs="?",
        default=None,
        help="Path to the output JSON file (default: input path with .json extension)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    file_path = args.input_pdf
    output_path = args.output_json or os.path.splitext(file_path)[0] + ".json"

    if not os.path.isfile(file_path):
        raise SystemExit(f"Input file not found: {file_path}")

    api_key = os.getenv("MISTRAL_API_KEY", "")

    if not api_key:
        raise SystemExit("MISTRAL_API_KEY not found in axters-mistral/api_key")

    client = Mistral(api_key=api_key)
    base64_file = encode_file(file_path)

    ocr_response = client.ocr.process(
        document={
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{base64_file}"
        },
        model="mistral-ocr-latest",
        include_image_base64=False,
        document_annotation_format=ResponseFormat(
            type="json_schema",
            json_schema=JSONSchema(
                name="response_schema",
                schema_definition={
                "properties": {
                    "items": {
                    "description": "Array of items",
                    "items": {
                        "properties": {
                        "definition": {
                            "description": "Item title",
                            "type": "string"
                        },
                        "lemma": {
                            "description": "meaning of the word in dutch",
                            "type": "string"
                        }
                        },
                        "required": [
                        "lemma",
                        "definition"
                        ],
                        "type": "object"
                    },
                    "type": "array"
                    }
                },
                "required": [
                    "items"
                ],
                "title": "ListResponse",
                "type": "object"
                },
                strict=True,
            ),
        ),
        include_blocks=False
    )

    # Save the full OCR response (pages, markdown, and document annotations) as JSON
    with open(output_path, "w", encoding="utf-8") as json_file:
        json.dump(ocr_response.model_dump(), json_file, ensure_ascii=False, indent=2)

    print(f"Processed {len(ocr_response.pages)} pages")
    print(f"OCR response written to: {output_path}")


if __name__ == "__main__":
    main()
